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
async def test_worker_enqueues_only_ai_selected_children_before_finishing_root() -> None:
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

    selector_calls: list[object] = []

    async def frontier_selector(**kwargs: object):
        selector_calls.append(kwargs)
        candidates = kwargs["candidates"]

        assert isinstance(candidates, list)
        assert kwargs["max_selected"] == min(len(candidates), 9)

        return [
            candidate
            for candidate in candidates
            if candidate.normalized_url.endswith(
                (
                    "/library/asyncio/",
                    "/library/task-scheduling.html",
                )
            )
        ]

    outcome = await process_next_job(
        worker_id="worker-frontier",
        lease_seconds=60,
        fetch_page=fetch_page,
        frontier_selector=frontier_selector,
    )

    assert outcome.state == "SUCCEEDED"
    assert outcome.job_id == root_job_id
    assert len(selector_calls) == 1

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
    assert len(jobs) == 3

    root_job = jobs[0]
    children = jobs[1:]

    assert root_job.id == root_job_id
    assert root_job.status == "SUCCEEDED"

    assert [child.normalized_url for child in children] == [
        "https://docs.example.com/library/asyncio/",
        "https://docs.example.com/library/task-scheduling.html",
    ]
    assert all(child.status == "PENDING" for child in children)
    assert all(child.depth == 1 for child in children)
    assert all(child.parent_job_id == root_job_id for child in children)

    # Completion must not close the run while selected P1 jobs remain.
    assert crawl_run.status == "RUNNING"


@pytest.mark.anyio
async def test_worker_never_auto_expands_selected_child_jobs() -> None:
    from app.crawler.worker_runtime import (
        FetchResult,
        PageArtifact,
        process_next_job,
    )

    crawl_run_id, _ = await create_root_crawl_job()

    root_html = b"""
        <html>
          <body>
            <a href="/library/follow-up.html">Follow-up evidence</a>
          </body>
        </html>
    """
    child_html = b"""
        <html>
          <body>
            <a href="/library/should-not-be-crawled.html">
              Nested discovery
            </a>
          </body>
        </html>
    """

    selector_calls: list[object] = []

    async def frontier_selector(**kwargs: object):
        selector_calls.append(kwargs)
        return list(kwargs["candidates"])

    async def fetch_page(
        url: str,
        timeout_seconds: int,
    ) -> FetchResult:
        body = (
            root_html
            if url.endswith("/overview.html")
            else child_html
        )

        return FetchResult(
            http_status_code=200,
            fetched_bytes=len(body),
            fetch_duration_ms=25,
            artifact=PageArtifact(
                content_type="text/html",
                body=body,
            ),
        )

    first = await process_next_job(
        worker_id="worker-frontier",
        lease_seconds=60,
        fetch_page=fetch_page,
        frontier_selector=frontier_selector,
    )
    # The domain politeness gate spaces same-domain requests by one second.
    # Let the selected P1 child become eligible before the next worker cycle.
    import asyncio

    await asyncio.sleep(1.1)

    second = await process_next_job(
        worker_id="worker-frontier",
        lease_seconds=60,
        fetch_page=fetch_page,
        frontier_selector=frontier_selector,
    )

    assert first.state == "SUCCEEDED"
    assert second.state == "SUCCEEDED"
    assert len(selector_calls) == 1

    async with session_factory() as session:
        jobs = list(
            await session.scalars(
                select(CrawlJob)
                .where(CrawlJob.crawl_run_id == crawl_run_id)
                .order_by(CrawlJob.depth.asc())
            )
        )

    assert len(jobs) == 2
    assert all(
        not job.normalized_url.endswith(
            "/library/should-not-be-crawled.html"
        )
        for job in jobs
    )


@pytest.mark.anyio
async def test_worker_completes_seed_without_mechanical_fallback_when_selector_fails() -> None:
    from app.crawler.worker_runtime import (
        FetchResult,
        PageArtifact,
        process_next_job,
    )

    crawl_run_id, root_job_id = await create_root_crawl_job()

    html = b"""
        <html>
          <body>
            <a href="/library/candidate.html">Possible evidence</a>
          </body>
        </html>
    """

    async def fetch_page(
        url: str,
        timeout_seconds: int,
    ) -> FetchResult:
        return FetchResult(
            http_status_code=200,
            fetched_bytes=len(html),
            fetch_duration_ms=25,
            artifact=PageArtifact(
                content_type="text/html",
                body=html,
            ),
        )

    async def failing_selector(**kwargs: object):
        raise RuntimeError("provider unavailable")

    outcome = await process_next_job(
        worker_id="worker-frontier",
        lease_seconds=60,
        fetch_page=fetch_page,
        frontier_selector=failing_selector,
    )

    assert outcome.state == "SUCCEEDED"
    assert outcome.job_id == root_job_id

    async with session_factory() as session:
        jobs = list(
            await session.scalars(
                select(CrawlJob)
                .where(CrawlJob.crawl_run_id == crawl_run_id)
            )
        )
        crawl_run = await session.get(CrawlRun, crawl_run_id)

    assert len(jobs) == 1
    assert crawl_run is not None
    assert crawl_run.status == "SUCCEEDED"
