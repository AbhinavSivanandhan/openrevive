from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID

from app.crawler.frontier_discovery import (
    DiscoveredLink,
    discover_links,
)
from app.crawler.frontier_persistence import enqueue_discovered_links
from app.crawler.frontier_selection import select_research_frontier
from app.crawler.job_failure import fail_job
from app.crawler.job_finalization import complete_job
from app.crawler.job_leasing import claim_next_job
from app.db.session import session_factory
from app.models.crawl_job import CrawlJob
from app.models.crawl_run import CrawlRun


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PageArtifact:
    """Bounded raw page content returned by a successful fetch."""

    content_type: str
    body: bytes


@dataclass(frozen=True, slots=True)
class FetchResult:
    http_status_code: int
    fetched_bytes: int
    fetch_duration_ms: int
    artifact: PageArtifact | None = None


class FetchFailure(Exception):
    def __init__(
        self,
        *,
        error_code: str,
        error_message: str,
    ) -> None:
        self.error_code = error_code
        self.error_message = error_message
        super().__init__(f"{error_code}: {error_message}")


@dataclass(frozen=True, slots=True)
class WorkerOutcome:
    state: str
    job_id: UUID | None


FetchPage = Callable[[str, int], Awaitable[FetchResult]]
JobClaimedCallback = Callable[[CrawlJob], Awaitable[None]]
PersistDocument = Callable[..., Awaitable[None]]
FrontierSelector = Callable[..., Awaitable[list[DiscoveredLink]]]

MAX_DISCOVERY_CANDIDATES = 250
MAX_FRONTIER_SELECTED_URLS = 12
FRONTIER_SELECTION_TARGET = 8


def is_html_artifact(artifact: PageArtifact) -> bool:
    media_type = artifact.content_type.lower().split(";", 1)[0].strip()
    return media_type == "text/html"


async def process_next_job(
    *,
    worker_id: str,
    lease_seconds: int,
    fetch_page: FetchPage,
    on_job_claimed: JobClaimedCallback | None = None,
    persist_document: PersistDocument | None = None,
    frontier_selector: FrontierSelector = select_research_frontier,
    max_discovery_candidates: int = MAX_DISCOVERY_CANDIDATES,
) -> WorkerOutcome:
    """
    Process at most one eligible crawl job.

    Network work occurs after the lease transaction commits, so slow remotes
    never keep PostgreSQL locks open.

    Frontier expansion happens before parent completion. That guarantees a
    root job cannot mark its campaign SUCCEEDED before durable child work has
    been inserted.
    """
    if max_discovery_candidates <= 0:
        raise ValueError(
            "max_discovery_candidates must be greater than zero"
        )

    async with session_factory() as session:
        claimed_job = await claim_next_job(
            session,
            worker_id=worker_id,
            lease_seconds=lease_seconds,
        )

    if claimed_job is None:
        return WorkerOutcome(state="IDLE", job_id=None)

    if claimed_job.lease_token is None:
        raise RuntimeError("claimed crawl job has no lease token")

    if on_job_claimed is not None:
        await on_job_claimed(claimed_job)

    async with session_factory() as session:
        crawl_run = await session.get(
            CrawlRun,
            claimed_job.crawl_run_id,
        )

    if crawl_run is None:
        raise RuntimeError("claimed crawl job has no crawl run")

    try:
        result = await fetch_page(
            claimed_job.normalized_url,
            crawl_run.request_timeout_seconds,
        )
    except FetchFailure as exc:
        async with session_factory() as session:
            failed_job = await fail_job(
                session,
                job_id=claimed_job.id,
                worker_id=worker_id,
                lease_token=claimed_job.lease_token,
                error_code=exc.error_code,
                error_message=exc.error_message,
            )

        return WorkerOutcome(
            state=failed_job.status,
            job_id=failed_job.id,
        )
    except Exception as exc:
        async with session_factory() as session:
            failed_job = await fail_job(
                session,
                job_id=claimed_job.id,
                worker_id=worker_id,
                lease_token=claimed_job.lease_token,
                error_code="WORKER_FETCH_ERROR",
                error_message=(
                    "unexpected fetch error: "
                    f"{exc.__class__.__name__}"
                ),
            )

        return WorkerOutcome(
            state=failed_job.status,
            job_id=failed_job.id,
        )

    if persist_document is not None:
        try:
            if result.artifact is None:
                raise RuntimeError(
                    "successful fetch did not return a page artifact"
                )

            await persist_document(
                crawl_run_id=claimed_job.crawl_run_id,
                crawl_job_id=claimed_job.id,
                artifact=result.artifact,
            )
        except Exception as exc:
            async with session_factory() as session:
                failed_job = await fail_job(
                    session,
                    job_id=claimed_job.id,
                    worker_id=worker_id,
                    lease_token=claimed_job.lease_token,
                    error_code="ARTIFACT_PERSISTENCE_ERROR",
                    error_message=(
                        "artifact persistence failed: "
                        f"{exc.__class__.__name__}"
                    ),
                )

            return WorkerOutcome(
                state=failed_job.status,
                job_id=failed_job.id,
            )

    artifact = result.artifact

    if (
        artifact is not None
        and claimed_job.depth == 0
        and crawl_run.research_intent is not None
        and is_html_artifact(artifact)
    ):
        try:
            candidates = discover_links(
                base_url=claimed_job.normalized_url,
                html=artifact.body,
                allowed_domains=crawl_run.allowed_domains,
                research_intent=crawl_run.research_intent,
                max_candidates=max_discovery_candidates,
                excluded_urls={claimed_job.normalized_url},
            )
        except Exception as exc:
            async with session_factory() as session:
                failed_job = await fail_job(
                    session,
                    job_id=claimed_job.id,
                    worker_id=worker_id,
                    lease_token=claimed_job.lease_token,
                    error_code="FRONTIER_DISCOVERY_ERROR",
                    error_message=(
                        "frontier discovery failed: "
                        f"{exc.__class__.__name__}"
                    ),
                )

            return WorkerOutcome(
                state=failed_job.status,
                job_id=failed_job.id,
            )

        selected_candidates = []

        if candidates:
            try:
                selected_candidates = await frontier_selector(
                    candidates=candidates,
                    research_intent=crawl_run.research_intent,
                    max_selected=min(
                        len(candidates),
                        max(0, crawl_run.max_pages - 1),
                        MAX_FRONTIER_SELECTED_URLS,
                    ),
                )
            except Exception as exc:
                logger.warning(
                    "Frontier selection failed for crawl run %s: %s",
                    crawl_run.id,
                    exc.__class__.__name__,
                )

        if selected_candidates:
            try:
                async with session_factory() as session:
                    await enqueue_discovered_links(
                        session,
                        parent_job_id=claimed_job.id,
                        candidates=selected_candidates,
                    )
            except Exception as exc:
                async with session_factory() as session:
                    failed_job = await fail_job(
                        session,
                        job_id=claimed_job.id,
                        worker_id=worker_id,
                        lease_token=claimed_job.lease_token,
                        error_code="FRONTIER_ENQUEUE_ERROR",
                        error_message=(
                            "frontier persistence failed: "
                            f"{exc.__class__.__name__}"
                        ),
                    )

                return WorkerOutcome(
                    state=failed_job.status,
                    job_id=failed_job.id,
                )

    async with session_factory() as session:
        completed_job = await complete_job(
            session,
            job_id=claimed_job.id,
            worker_id=worker_id,
            lease_token=claimed_job.lease_token,
            http_status_code=result.http_status_code,
            fetched_bytes=result.fetched_bytes,
            fetch_duration_ms=result.fetch_duration_ms,
        )

    return WorkerOutcome(
        state=completed_job.status,
        job_id=completed_job.id,
    )
