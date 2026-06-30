from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app.crawler.job_finalization import LeaseLostError
from app.crawler.job_leasing import claim_next_job
from app.db.session import session_factory
from app.models.collection import Collection
from app.models.crawl_domain_policy import CrawlDomainPolicy
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
        workspace = Workspace(name=f"Failure Test {uuid4()}")
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
            seed_urls=["https://example.com/failure"],
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
            original_url="https://example.com/failure",
            normalized_url="https://example.com/failure",
            domain="example.com",
            depth=0,
            max_attempts=max_attempts,
        )
        session.add(job)
        await session.commit()

        return crawl_run.id, job.id


async def make_domain_ready(domain: str) -> None:
    async with session_factory() as session:
        policy = await session.get(
            CrawlDomainPolicy,
            domain,
        )

        assert policy is not None

        policy.next_allowed_at = (
            datetime.now(UTC) - timedelta(seconds=1)
        )

        await session.commit()


@pytest.mark.anyio
async def test_failure_before_retry_budget_requeues_job() -> None:
    from app.crawler.job_failure import fail_job

    _, _ = await create_crawl_run_with_job(max_attempts=3)

    async with session_factory() as session:
        claimed_job = await claim_next_job(
            session,
            worker_id="worker-a",
            lease_seconds=60,
        )

    assert claimed_job is not None
    assert claimed_job.lease_token is not None

    async with session_factory() as session:
        failed_job = await fail_job(
            session,
            job_id=claimed_job.id,
            worker_id="worker-a",
            lease_token=claimed_job.lease_token,
            error_code="HTTP_TIMEOUT",
            error_message="upstream request timed out",
        )

    assert failed_job.status == "RETRY_PENDING"
    assert failed_job.attempt_count == 1
    assert failed_job.lease_owner is None
    assert failed_job.lease_token is None
    assert failed_job.lease_expires_at is None
    assert failed_job.last_error_code == "HTTP_TIMEOUT"
    assert failed_job.last_error_message == "upstream request timed out"
    assert failed_job.finished_at is None


@pytest.mark.anyio
async def test_retry_pending_job_can_be_claimed_again() -> None:
    from app.crawler.job_failure import fail_job

    _, _ = await create_crawl_run_with_job(max_attempts=3)

    async with session_factory() as session:
        first_claim = await claim_next_job(
            session,
            worker_id="worker-a",
            lease_seconds=60,
        )

    assert first_claim is not None
    assert first_claim.lease_token is not None

    async with session_factory() as session:
        await fail_job(
            session,
            job_id=first_claim.id,
            worker_id="worker-a",
            lease_token=first_claim.lease_token,
            error_code="HTTP_TIMEOUT",
            error_message="temporary failure",
        )

    await make_domain_ready("example.com")

    async with session_factory() as session:
        second_claim = await claim_next_job(
            session,
            worker_id="worker-b",
            lease_seconds=60,
        )

    assert second_claim is not None
    assert second_claim.id == first_claim.id
    assert second_claim.status == "LEASED"
    assert second_claim.attempt_count == 2
    assert second_claim.lease_owner == "worker-b"


@pytest.mark.anyio
async def test_final_failure_marks_job_and_run_failed() -> None:
    from app.crawler.job_failure import fail_job

    crawl_run_id, _ = await create_crawl_run_with_job(max_attempts=1)

    async with session_factory() as session:
        claimed_job = await claim_next_job(
            session,
            worker_id="worker-a",
            lease_seconds=60,
        )

    assert claimed_job is not None
    assert claimed_job.lease_token is not None

    async with session_factory() as session:
        failed_job = await fail_job(
            session,
            job_id=claimed_job.id,
            worker_id="worker-a",
            lease_token=claimed_job.lease_token,
            error_code="DNS_ERROR",
            error_message="host could not be resolved",
        )

    assert failed_job.status == "FAILED"
    assert failed_job.finished_at is not None
    assert failed_job.lease_owner is None
    assert failed_job.lease_token is None
    assert failed_job.lease_expires_at is None

    async with session_factory() as session:
        crawl_run = await session.get(CrawlRun, crawl_run_id)

    assert crawl_run is not None
    assert crawl_run.status == "FAILED"
    assert crawl_run.completed_at is not None


@pytest.mark.anyio
async def test_failure_rejects_stale_lease_ownership() -> None:
    from app.crawler.job_failure import fail_job

    _, _ = await create_crawl_run_with_job()

    async with session_factory() as session:
        claimed_job = await claim_next_job(
            session,
            worker_id="worker-a",
            lease_seconds=60,
        )

    assert claimed_job is not None

    async with session_factory() as session:
        with pytest.raises(LeaseLostError):
            await fail_job(
                session,
                job_id=claimed_job.id,
                worker_id="worker-a",
                lease_token=uuid4(),
                error_code="HTTP_TIMEOUT",
                error_message="stale worker result",
            )

    async with session_factory() as session:
        job = await session.get(CrawlJob, claimed_job.id)

    assert job is not None
    assert job.status == "LEASED"
    assert job.lease_owner == "worker-a"


@pytest.mark.anyio
async def test_mixed_terminal_results_mark_crawl_run_partially_succeeded() -> None:
    from app.crawler.job_failure import fail_job
    from app.crawler.job_finalization import complete_job

    crawl_run_id, _ = await create_crawl_run_with_job(max_attempts=1)

    async with session_factory() as session:
        second_job = CrawlJob(
            crawl_run_id=crawl_run_id,
            original_url="https://other.example.com/succeeds",
            normalized_url="https://other.example.com/succeeds",
            domain="other.example.com",
            depth=0,
            max_attempts=1,
        )
        session.add(second_job)
        await session.commit()

    async with session_factory() as session:
        successful_job = await claim_next_job(
            session,
            worker_id="worker-success",
            lease_seconds=60,
        )

    assert successful_job is not None
    assert successful_job.lease_token is not None

    async with session_factory() as session:
        completed_job = await complete_job(
            session,
            job_id=successful_job.id,
            worker_id="worker-success",
            lease_token=successful_job.lease_token,
            http_status_code=200,
            fetched_bytes=2_048,
            fetch_duration_ms=40,
        )

    assert completed_job.status == "SUCCEEDED"

    async with session_factory() as session:
        failed_job = await claim_next_job(
            session,
            worker_id="worker-failure",
            lease_seconds=60,
        )

    assert failed_job is not None
    assert failed_job.lease_token is not None
    assert failed_job.id != completed_job.id

    async with session_factory() as session:
        exhausted_job = await fail_job(
            session,
            job_id=failed_job.id,
            worker_id="worker-failure",
            lease_token=failed_job.lease_token,
            error_code="HTTP_503",
            error_message="upstream service unavailable",
        )

    assert exhausted_job.status == "FAILED"

    async with session_factory() as session:
        crawl_run = await session.get(CrawlRun, crawl_run_id)

    assert crawl_run is not None
    assert crawl_run.status == "PARTIALLY_SUCCEEDED"
    assert crawl_run.completed_at is not None


@pytest.mark.anyio
async def test_failure_after_campaign_cancel_does_not_requeue_job() -> None:
    from app.crawler.job_failure import fail_job

    crawl_run_id, _ = await create_crawl_run_with_job(
        max_attempts=3,
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
        crawl_run = await session.get(CrawlRun, crawl_run_id)
        assert crawl_run is not None
        crawl_run.status = "CANCELLED"
        await session.commit()

    async with session_factory() as session:
        failed_job = await fail_job(
            session,
            job_id=claimed_job.id,
            worker_id="worker-a",
            lease_token=claimed_job.lease_token,
            error_code="HTTP_TIMEOUT",
            error_message="campaign was cancelled in flight",
        )

    assert failed_job.status == "CANCELLED"

    async with session_factory() as session:
        crawl_run = await session.get(CrawlRun, crawl_run_id)

    assert crawl_run is not None
    assert crawl_run.status == "CANCELLED"
