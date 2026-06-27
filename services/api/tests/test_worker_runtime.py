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


async def create_crawl_run_with_job(
    *,
    max_attempts: int = 3,
) -> tuple[UUID, UUID]:
    async with session_factory() as session:
        workspace = Workspace(
            name=f"Worker Runtime Test {uuid4()}"
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
            seed_urls=["https://example.com/page"],
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
            original_url="https://example.com/page",
            normalized_url="https://example.com/page",
            domain="example.com",
            depth=0,
            max_attempts=max_attempts,
        )
        session.add(job)
        await session.commit()

        return crawl_run.id, job.id


@pytest.mark.anyio
async def test_process_next_job_returns_idle_when_no_jobs_exist() -> None:
    from app.crawler.worker_runtime import (
        FetchResult,
        process_next_job,
    )

    fetch_called = False

    async def fetch_page(
        url: str,
        timeout_seconds: int,
    ) -> FetchResult:
        nonlocal fetch_called
        fetch_called = True
        raise AssertionError("fetcher must not run when queue is empty")

    outcome = await process_next_job(
        worker_id="worker-idle",
        lease_seconds=60,
        fetch_page=fetch_page,
    )

    assert outcome.state == "IDLE"
    assert outcome.job_id is None
    assert fetch_called is False


@pytest.mark.anyio
async def test_process_next_job_completes_a_successfully_fetched_job() -> None:
    from app.crawler.worker_runtime import (
        FetchResult,
        process_next_job,
    )

    _, job_id = await create_crawl_run_with_job()
    observed: dict[str, object] = {}

    async def fetch_page(
        url: str,
        timeout_seconds: int,
    ) -> FetchResult:
        observed["url"] = url
        observed["timeout_seconds"] = timeout_seconds

        return FetchResult(
            http_status_code=200,
            fetched_bytes=4_096,
            fetch_duration_ms=125,
        )

    outcome = await process_next_job(
        worker_id="worker-success",
        lease_seconds=60,
        fetch_page=fetch_page,
    )

    assert outcome.state == "SUCCEEDED"
    assert outcome.job_id == job_id
    assert observed == {
        "url": "https://example.com/page",
        "timeout_seconds": 15,
    }

    async with session_factory() as session:
        job = await session.get(CrawlJob, job_id)

    assert job is not None
    assert job.status == "SUCCEEDED"
    assert job.http_status_code == 200
    assert job.fetched_bytes == 4_096
    assert job.fetch_duration_ms == 125


@pytest.mark.anyio
async def test_process_next_job_records_a_fetch_failure() -> None:
    from app.crawler.worker_runtime import (
        FetchFailure,
        FetchResult,
        process_next_job,
    )

    crawl_run_id, job_id = await create_crawl_run_with_job(
        max_attempts=1,
    )

    async def fetch_page(
        url: str,
        timeout_seconds: int,
    ) -> FetchResult:
        raise FetchFailure(
            error_code="HTTP_TIMEOUT",
            error_message="upstream request timed out",
        )

    outcome = await process_next_job(
        worker_id="worker-failure",
        lease_seconds=60,
        fetch_page=fetch_page,
    )

    assert outcome.state == "FAILED"
    assert outcome.job_id == job_id

    async with session_factory() as session:
        job = await session.get(CrawlJob, job_id)
        crawl_run = await session.get(CrawlRun, crawl_run_id)

    assert job is not None
    assert job.status == "FAILED"
    assert job.last_error_code == "HTTP_TIMEOUT"
    assert job.last_error_message == "upstream request timed out"

    assert crawl_run is not None
    assert crawl_run.status == "FAILED"


@pytest.mark.anyio
async def test_process_next_job_persists_artifact_before_completing_job() -> None:
    from app.crawler.worker_runtime import (
        FetchResult,
        PageArtifact,
        process_next_job,
    )

    crawl_run_id, job_id = await create_crawl_run_with_job()
    observed: dict[str, object] = {}

    async def fetch_page(
        url: str,
        timeout_seconds: int,
    ) -> FetchResult:
        return FetchResult(
            http_status_code=200,
            fetched_bytes=48,
            fetch_duration_ms=25,
            artifact=PageArtifact(
                content_type="text/html",
                body=b"<html><body>OpenRevive</body></html>",
            ),
        )

    async def persist_document(
        *,
        crawl_run_id: UUID,
        crawl_job_id: UUID,
        artifact: PageArtifact,
    ) -> None:
        observed["crawl_run_id"] = crawl_run_id
        observed["crawl_job_id"] = crawl_job_id
        observed["artifact"] = artifact

        async with session_factory() as session:
            job = await session.get(CrawlJob, crawl_job_id)

        assert job is not None
        assert job.status == "LEASED"

    outcome = await process_next_job(
        worker_id="worker-persist-success",
        lease_seconds=60,
        fetch_page=fetch_page,
        persist_document=persist_document,
    )

    assert outcome.state == "SUCCEEDED"
    assert outcome.job_id == job_id
    assert observed["crawl_run_id"] == crawl_run_id
    assert observed["crawl_job_id"] == job_id

    stored_artifact = observed["artifact"]
    assert isinstance(stored_artifact, PageArtifact)
    assert stored_artifact.body == (
        b"<html><body>OpenRevive</body></html>"
    )


@pytest.mark.anyio
async def test_process_next_job_retries_when_artifact_persistence_fails() -> None:
    from app.crawler.worker_runtime import (
        FetchResult,
        PageArtifact,
        process_next_job,
    )

    _, job_id = await create_crawl_run_with_job(
        max_attempts=2,
    )

    async def fetch_page(
        url: str,
        timeout_seconds: int,
    ) -> FetchResult:
        return FetchResult(
            http_status_code=200,
            fetched_bytes=48,
            fetch_duration_ms=25,
            artifact=PageArtifact(
                content_type="text/html",
                body=b"<html><body>OpenRevive</body></html>",
            ),
        )

    async def persist_document(
        *,
        crawl_run_id: UUID,
        crawl_job_id: UUID,
        artifact: PageArtifact,
    ) -> None:
        raise RuntimeError("MinIO is unavailable")

    outcome = await process_next_job(
        worker_id="worker-persist-failure",
        lease_seconds=60,
        fetch_page=fetch_page,
        persist_document=persist_document,
    )

    assert outcome.state == "RETRY_PENDING"
    assert outcome.job_id == job_id

    async with session_factory() as session:
        job = await session.get(CrawlJob, job_id)

    assert job is not None
    assert job.status == "RETRY_PENDING"
    assert job.last_error_code == "ARTIFACT_PERSISTENCE_ERROR"
    assert "RuntimeError" in job.last_error_message
