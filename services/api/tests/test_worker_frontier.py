from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

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


async def create_root_crawl_job() -> tuple[UUID, UUID]:
    async with session_factory() as session:
        workspace = Workspace(
            name=f"Worker Frontier {uuid4()}",
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
            seed_urls=[
                "https://docs.example.com/library/overview.html",
            ],
            allowed_domains=["docs.example.com"],
            research_intent="async task scheduling",
            max_pages=10,
            max_depth=2,
            request_timeout_seconds=15,
            max_attempts=2,
            idempotency_key=str(uuid4()),
            started_at=datetime.now(UTC),
        )
        session.add(crawl_run)
        await session.flush()

        root_job = CrawlJob(
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
        session.add(root_job)
        await session.commit()

        return crawl_run.id, root_job.id


@pytest.mark.anyio
async def test_worker_enqueues_ranked_children_before_finishing_root() -> None:
    from app.crawler.worker_runtime import (
        FetchResult,
        PageArtifact,
        process_next_job,
    )

    crawl_run_id, root_job_id = await create_root_crawl_job()

    html = b"""
        <html>
          <body>
            <a href="/library/asyncio/">
              Asyncio task groups and scheduling
            </a>

            <a href="/library/task-scheduling.html">
              Task scheduling patterns
            </a>

            <a href="/library/red-soil.html">
              Red soil reference
            </a>

            <a href="https://outside.example.net/ignore.html">
              External page
            </a>

            <a href="/library/next.html">Next</a>
          </body>
        </html>
    """

    async def fetch_page(
        url: str,
        timeout_seconds: int,
    ) -> FetchResult:
        assert url == (
            "https://docs.example.com/library/overview.html"
        )
        assert timeout_seconds == 15

        return FetchResult(
            http_status_code=200,
            fetched_bytes=len(html),
            fetch_duration_ms=25,
            artifact=PageArtifact(
                content_type="text/html",
                body=html,
            ),
        )

    outcome = await process_next_job(
        worker_id="worker-frontier",
        lease_seconds=60,
        fetch_page=fetch_page,
    )

    assert outcome.state == "SUCCEEDED"
    assert outcome.job_id == root_job_id

    async with session_factory() as session:
        crawl_run = await session.get(CrawlRun, crawl_run_id)

        jobs = list(
            await session.scalars(
                select(CrawlJob)
                .where(CrawlJob.crawl_run_id == crawl_run_id)
                .order_by(
                    CrawlJob.depth.asc(),
                    CrawlJob.priority_score.desc(),
                    CrawlJob.normalized_url.asc(),
                )
            )
        )

    assert crawl_run is not None

    # Root plus three valid, in-scope, non-navigation discoveries.
    assert len(jobs) == 4

    root_job = jobs[0]
    children = jobs[1:]

    assert root_job.id == root_job_id
    assert root_job.status == "SUCCEEDED"

    assert [child.normalized_url for child in children] == [
        "https://docs.example.com/library/asyncio/",
        "https://docs.example.com/library/task-scheduling.html",
        "https://docs.example.com/library/red-soil.html",
    ]

    assert [child.priority_band for child in children] == [
        "CORE",
        "RELATED",
        "LOW",
    ]

    assert all(child.status == "PENDING" for child in children)
    assert all(child.depth == 1 for child in children)
    assert all(child.parent_job_id == root_job_id for child in children)

    assert children[0].anchor_text == (
        "Asyncio task groups and scheduling"
    )
    assert children[0].discovery_reason == (
        "anchor and URL match research intent"
    )

    # Completion must not close the run while P1 jobs are queued.
    assert crawl_run.status == "RUNNING"
