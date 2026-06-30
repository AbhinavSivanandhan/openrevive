from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.briefing.campaign_brief_service import (
    NoUsableCampaignEvidenceError,
    reserve_campaign_brief,
)
from app.db.session import session_factory
from app.models.campaign_brief import CampaignBrief
from app.models.collection import Collection
from app.models.crawled_document import CrawledDocument
from app.models.crawl_job import CrawlJob
from app.models.crawl_run import CrawlRun
from app.models.workspace import Workspace


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def create_completed_campaign(
    *,
    extracted_text: str = (
        "OpenRevive uses database-backed leases to coordinate "
        "distributed crawler workers."
    ),
    content_sha256: str = "a" * 64,
) -> UUID:
    async with session_factory() as session:
        workspace = Workspace(
            name=f"Brief Workspace {uuid4()}",
        )
        session.add(workspace)
        await session.flush()

        collection = Collection(
            workspace_id=workspace.id,
            name=f"Brief Collection {uuid4()}",
        )
        session.add(collection)
        await session.flush()

        now = datetime.now(UTC)

        crawl_run = CrawlRun(
            collection_id=collection.id,
            status="SUCCEEDED",
            name="Distributed Crawler Research",
            research_intent=(
                "Explain distributed crawler coordination."
            ),
            seed_urls=["https://docs.example.com/start"],
            allowed_domains=["docs.example.com"],
            max_pages=10,
            max_depth=1,
            request_timeout_seconds=15,
            max_attempts=3,
            idempotency_key=str(uuid4()),
            started_at=now,
            completed_at=now,
        )
        session.add(crawl_run)
        await session.flush()

        crawl_job = CrawlJob(
            crawl_run_id=crawl_run.id,
            original_url="https://docs.example.com/start",
            normalized_url="https://docs.example.com/start",
            domain="docs.example.com",
            depth=0,
            status="SUCCEEDED",
            max_attempts=3,
            finished_at=now,
        )
        session.add(crawl_job)
        await session.flush()

        session.add(
            CrawledDocument(
                crawl_job_id=crawl_job.id,
                raw_object_key=(
                    f"crawl-runs/{crawl_run.id}/"
                    f"jobs/{crawl_job.id}/raw.html"
                ),
                content_type="text/html",
                content_sha256=content_sha256,
                title="Worker coordination",
                extracted_text=extracted_text,
            )
        )

        await session.commit()

        return crawl_run.id


@pytest.mark.anyio
async def test_reservation_is_cached_for_an_unchanged_campaign() -> None:
    crawl_run_id = await create_completed_campaign()

    async with session_factory() as session:
        crawl_run = await session.get(CrawlRun, crawl_run_id)
        assert crawl_run is not None

        first = await reserve_campaign_brief(
            session,
            crawl_run=crawl_run,
        )
        await session.commit()

    async with session_factory() as session:
        crawl_run = await session.get(CrawlRun, crawl_run_id)
        assert crawl_run is not None

        second = await reserve_campaign_brief(
            session,
            crawl_run=crawl_run,
        )
        await session.commit()

        brief_count = await session.scalar(
            select(func.count())
            .select_from(CampaignBrief)
            .where(CampaignBrief.crawl_run_id == crawl_run_id)
        )

    assert first.created is True
    assert second.created is False
    assert first.brief.id == second.brief.id
    assert first.brief.status == "GENERATING"
    assert second.brief.status == "GENERATING"
    assert brief_count == 1


@pytest.mark.anyio
async def test_changed_evidence_creates_a_new_reservation() -> None:
    crawl_run_id = await create_completed_campaign()

    async with session_factory() as session:
        crawl_run = await session.get(CrawlRun, crawl_run_id)
        assert crawl_run is not None

        first = await reserve_campaign_brief(
            session,
            crawl_run=crawl_run,
        )
        await session.commit()

    async with session_factory() as session:
        document = await session.scalar(
            select(CrawledDocument)
            .join(CrawlJob)
            .where(CrawlJob.crawl_run_id == crawl_run_id)
        )

        assert document is not None

        document.content_sha256 = "b" * 64
        await session.commit()

    async with session_factory() as session:
        crawl_run = await session.get(CrawlRun, crawl_run_id)
        assert crawl_run is not None

        second = await reserve_campaign_brief(
            session,
            crawl_run=crawl_run,
        )
        await session.commit()

    assert first.created is True
    assert second.created is True
    assert first.brief.id != second.brief.id
    assert (
        first.brief.corpus_fingerprint
        != second.brief.corpus_fingerprint
    )


@pytest.mark.anyio
async def test_empty_campaign_evidence_is_not_reserved() -> None:
    crawl_run_id = await create_completed_campaign(
        extracted_text="   ",
    )

    async with session_factory() as session:
        crawl_run = await session.get(CrawlRun, crawl_run_id)
        assert crawl_run is not None

        with pytest.raises(
            NoUsableCampaignEvidenceError,
            match="no persisted extracted text",
        ):
            await reserve_campaign_brief(
                session,
                crawl_run=crawl_run,
            )

        brief_count = await session.scalar(
            select(func.count()).select_from(CampaignBrief)
        )

    assert brief_count == 0
