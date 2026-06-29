from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.crawler.frontier_discovery import DiscoveredLink
from app.db.session import session_factory
from app.models.collection import Collection
from app.models.crawl_job import CrawlJob
from app.models.crawl_run import CrawlRun
from app.models.workspace import Workspace


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def create_parent_job(
    *,
    max_pages: int = 10,
    max_depth: int = 2,
) -> tuple[UUID, UUID]:
    async with session_factory() as session:
        workspace = Workspace(name=f"Frontier Persistence {uuid4()}")
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
            seed_urls=[
                "https://docs.example.com/library/overview.html",
            ],
            allowed_domains=["docs.example.com"],
            max_pages=max_pages,
            max_depth=max_depth,
            request_timeout_seconds=15,
            max_attempts=2,
            idempotency_key=str(uuid4()),
            started_at=datetime.now(UTC),
        )
        session.add(crawl_run)
        await session.flush()

        parent_job = CrawlJob(
            crawl_run_id=crawl_run.id,
            original_url=(
                "https://docs.example.com/library/overview.html"
            ),
            normalized_url=(
                "https://docs.example.com/library/overview.html"
            ),
            domain="docs.example.com",
            depth=0,
            max_attempts=2,
        )
        session.add(parent_job)
        await session.commit()

        return crawl_run.id, parent_job.id


@pytest.mark.anyio
async def test_enqueue_discovered_links_creates_ranked_child_jobs() -> None:
    from app.crawler.frontier_persistence import (
        enqueue_discovered_links,
    )

    crawl_run_id, parent_job_id = await create_parent_job()

    candidates = [
        DiscoveredLink(
            normalized_url=(
                "https://docs.example.com/library/asyncio/"
            ),
            anchor_text="Asyncio task groups",
            priority_score=140,
            priority_band="CORE",
            reason="anchor and URL match research intent",
        ),
        DiscoveredLink(
            normalized_url=(
                "https://docs.example.com/"
                "library/task-scheduling.html"
            ),
            anchor_text="Task scheduling patterns",
            priority_score=80,
            priority_band="RELATED",
            reason="partial research-intent match",
        ),
    ]

    async with session_factory() as session:
        inserted_job_ids = await enqueue_discovered_links(
            session,
            parent_job_id=parent_job_id,
            candidates=candidates,
        )

    assert len(inserted_job_ids) == 2

    async with session_factory() as session:
        child_jobs = list(
            await session.scalars(
                select(CrawlJob)
                .where(
                    CrawlJob.crawl_run_id == crawl_run_id,
                    CrawlJob.parent_job_id == parent_job_id,
                )
                .order_by(CrawlJob.priority_score.desc())
            )
        )

    assert [job.normalized_url for job in child_jobs] == [
        "https://docs.example.com/library/asyncio/",
        "https://docs.example.com/library/task-scheduling.html",
    ]

    assert all(job.depth == 1 for job in child_jobs)
    assert all(job.status == "PENDING" for job in child_jobs)
    assert all(job.max_attempts == 2 for job in child_jobs)

    assert child_jobs[0].anchor_text == "Asyncio task groups"
    assert child_jobs[0].priority_score == 140
    assert child_jobs[0].priority_band == "CORE"
    assert child_jobs[0].discovery_reason == (
        "anchor and URL match research intent"
    )


@pytest.mark.anyio
async def test_enqueue_discovered_links_respects_dedupe_depth_and_page_budget() -> None:
    from app.crawler.frontier_persistence import (
        enqueue_discovered_links,
    )

    crawl_run_id, parent_job_id = await create_parent_job(
        max_pages=3,
        max_depth=1,
    )

    candidates = [
        DiscoveredLink(
            normalized_url=(
                "https://docs.example.com/library/asyncio/"
            ),
            anchor_text="Asyncio",
            priority_score=140,
            priority_band="CORE",
            reason="high relevance",
        ),
        DiscoveredLink(
            normalized_url=(
                "https://docs.example.com/"
                "library/task-scheduling.html"
            ),
            anchor_text="Task scheduling",
            priority_score=80,
            priority_band="RELATED",
            reason="related",
        ),
        DiscoveredLink(
            normalized_url=(
                "https://docs.example.com/library/low-priority.html"
            ),
            anchor_text="Low priority",
            priority_score=5,
            priority_band="LOW",
            reason="weak relevance",
        ),
    ]

    async with session_factory() as session:
        first_inserted = await enqueue_discovered_links(
            session,
            parent_job_id=parent_job_id,
            candidates=candidates,
        )

    # One seed already exists. max_pages=3 leaves room for only two children.
    assert len(first_inserted) == 2

    async with session_factory() as session:
        second_inserted = await enqueue_discovered_links(
            session,
            parent_job_id=parent_job_id,
            candidates=candidates,
        )

    assert second_inserted == []

    async with session_factory() as session:
        all_jobs = list(
            await session.scalars(
                select(CrawlJob)
                .where(CrawlJob.crawl_run_id == crawl_run_id)
                .order_by(CrawlJob.depth, CrawlJob.priority_score.desc())
            )
        )

    assert len(all_jobs) == 3
    assert [job.normalized_url for job in all_jobs[1:]] == [
        "https://docs.example.com/library/asyncio/",
        "https://docs.example.com/library/task-scheduling.html",
    ]

    # A depth-1 child cannot create more jobs when max_depth is 1.
    child_job_id = all_jobs[1].id

    async with session_factory() as session:
        at_depth_limit = await enqueue_discovered_links(
            session,
            parent_job_id=child_job_id,
            candidates=[
                DiscoveredLink(
                    normalized_url=(
                        "https://docs.example.com/"
                        "library/nested-detail.html"
                    ),
                    anchor_text="Nested detail",
                    priority_score=100,
                    priority_band="CORE",
                    reason="would be relevant",
                )
            ],
        )

    assert at_depth_limit == []
