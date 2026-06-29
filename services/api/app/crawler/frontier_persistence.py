from __future__ import annotations

from collections.abc import Sequence
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.crawler.frontier_discovery import DiscoveredLink
from app.models.crawl_job import CrawlJob
from app.models.crawl_run import CrawlRun


def _candidate_order_key(candidate: DiscoveredLink) -> tuple[int, str]:
    return (-candidate.priority_score, candidate.normalized_url)


def _deduplicate_candidates(
    candidates: Sequence[DiscoveredLink],
) -> list[DiscoveredLink]:
    """
    Keep one candidate per canonical URL before touching the database.

    Database uniqueness remains the final concurrency-safe guard. This local
    pass only avoids wasting the campaign budget on repeated links from one
    extracted page.
    """
    best_by_url: dict[str, DiscoveredLink] = {}

    for candidate in candidates:
        existing = best_by_url.get(candidate.normalized_url)

        if (
            existing is None
            or _candidate_order_key(candidate)
            < _candidate_order_key(existing)
        ):
            best_by_url[candidate.normalized_url] = candidate

    return sorted(
        best_by_url.values(),
        key=_candidate_order_key,
    )


def _domain_for_url(normalized_url: str) -> str:
    hostname = (urlsplit(normalized_url).hostname or "").lower().rstrip(".")

    if not hostname:
        raise ValueError(
            f"discovered candidate has no hostname: {normalized_url}"
        )

    return hostname


async def enqueue_discovered_links(
    session: AsyncSession,
    *,
    parent_job_id: UUID,
    candidates: Sequence[DiscoveredLink],
) -> list[UUID]:
    """
    Persist discovery candidates as campaign-scoped child jobs.

    The crawl-run row is locked while applying the max-pages budget. That
    serializes concurrent worker expansion within one campaign, preventing
    several workers from independently deciding that budget remains.
    """
    if not candidates:
        return []

    async with session.begin():
        parent_job = await session.scalar(
            select(CrawlJob)
            .where(CrawlJob.id == parent_job_id)
            .with_for_update()
        )

        if parent_job is None:
            raise ValueError(
                f"parent crawl job does not exist: {parent_job_id}"
            )

        crawl_run = await session.scalar(
            select(CrawlRun)
            .where(CrawlRun.id == parent_job.crawl_run_id)
            .with_for_update()
        )

        if crawl_run is None:
            raise ValueError(
                "parent crawl job references a missing crawl run"
            )

        # A paused worker may finish its current fetch. Let it persist
        # discovered children so resume can continue the frontier, but never
        # expand a terminal or cancelled campaign.
        if crawl_run.status not in {"RUNNING", "PAUSED"}:
            return []

        if parent_job.depth >= crawl_run.max_depth:
            return []

        total_jobs = int(
            await session.scalar(
                select(func.count(CrawlJob.id)).where(
                    CrawlJob.crawl_run_id == crawl_run.id
                )
            )
            or 0
        )

        remaining_capacity = crawl_run.max_pages - total_jobs

        if remaining_capacity <= 0:
            return []

        ordered_candidates = _deduplicate_candidates(candidates)
        candidate_urls = [
            candidate.normalized_url
            for candidate in ordered_candidates
        ]

        existing_urls = set(
            (
                await session.scalars(
                    select(CrawlJob.normalized_url).where(
                        CrawlJob.crawl_run_id == crawl_run.id,
                        CrawlJob.normalized_url.in_(candidate_urls),
                    )
                )
            ).all()
        )

        new_candidates = [
            candidate
            for candidate in ordered_candidates
            if candidate.normalized_url not in existing_urls
        ][:remaining_capacity]

        if not new_candidates:
            return []

        rows = [
            {
                "crawl_run_id": crawl_run.id,
                "parent_job_id": parent_job.id,
                # Discovery candidates are already canonical URLs. We retain
                # that exact value as original_url until we later persist the
                # raw href separately, if useful for provenance.
                "original_url": candidate.normalized_url,
                "normalized_url": candidate.normalized_url,
                "domain": _domain_for_url(candidate.normalized_url),
                "depth": parent_job.depth + 1,
                "status": "PENDING",
                "max_attempts": crawl_run.max_attempts,
                "anchor_text": candidate.anchor_text or None,
                "priority_score": candidate.priority_score,
                "priority_band": candidate.priority_band,
                "discovery_reason": candidate.reason,
            }
            for candidate in new_candidates
        ]

        statement = (
            insert(CrawlJob)
            .values(rows)
            .on_conflict_do_nothing(
                constraint="crawl_run_normalized_url_unique"
            )
            .returning(CrawlJob.id)
        )

        result = await session.execute(statement)

        return list(result.scalars())
