from __future__ import annotations

from app.crawler.worker_heartbeats import (
    record_heartbeat,
    register_worker,
)
from app.crawler.worker_runtime import (
    FetchPage,
    PersistDocument,
    WorkerOutcome,
    process_next_job,
)
from app.db.session import session_factory
from app.models.crawl_job import CrawlJob


async def run_worker_cycle(
    *,
    worker_id: str,
    lease_seconds: int,
    fetch_page: FetchPage,
    persist_document: PersistDocument | None = None,
    register_worker_if_needed: bool = True,
) -> WorkerOutcome:
    """
    Run one observable worker cycle.

    The worker registers before processing. It reports PROCESSING only after
    a durable lease is acquired, and always returns to IDLE after the cycle.
    Lease ownership remains authoritative; the heartbeat is operational
    visibility metadata.
    """
    if register_worker_if_needed:
        async with session_factory() as session:
            await register_worker(
                session,
                worker_id=worker_id,
            )

    async def report_processing(job: CrawlJob) -> None:
        async with session_factory() as session:
            await record_heartbeat(
                session,
                worker_id=worker_id,
                status="PROCESSING",
                current_job_id=job.id,
            )

    try:
        return await process_next_job(
            worker_id=worker_id,
            lease_seconds=lease_seconds,
            fetch_page=fetch_page,
            on_job_claimed=report_processing,
            persist_document=persist_document,
        )
    finally:
        async with session_factory() as session:
            await record_heartbeat(
                session,
                worker_id=worker_id,
                status="IDLE",
            )
