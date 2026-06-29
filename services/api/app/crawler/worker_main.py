from __future__ import annotations

import asyncio
import logging
import os
import signal
from collections.abc import Awaitable, Callable
from uuid import UUID

from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.crawler.document_persistence import (
    ArtifactStore,
    persist_crawled_document,
)
from app.crawler.minio_artifact_store import build_s3_artifact_store
from app.crawler.worker_runtime import PageArtifact
from app.db.session import session_factory
from app.models.crawled_document import CrawledDocument
from app.crawler.worker_entrypoint import (
    resolve_worker_id,
    run_worker_process,
)

logger = logging.getLogger(__name__)

DEFAULT_LEASE_SECONDS = 180
DEFAULT_IDLE_POLL_SECONDS = 1.0
DEFAULT_MAX_RESPONSE_BYTES = 2_000_000

WorkerProcess = Callable[..., Awaitable[None]]
ArtifactStoreFactory = Callable[[Settings], ArtifactStore]


def read_positive_int(
    environment_variable: str,
    *,
    default: int,
) -> int:
    raw_value = os.getenv(environment_variable, str(default))

    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(
            f"{environment_variable} must be a positive integer"
        ) from exc

    if value <= 0:
        raise ValueError(
            f"{environment_variable} must be greater than zero"
        )

    return value


def read_positive_float(
    environment_variable: str,
    *,
    default: float,
) -> float:
    raw_value = os.getenv(environment_variable, str(default))

    try:
        value = float(raw_value)
    except ValueError as exc:
        raise ValueError(
            f"{environment_variable} must be a positive number"
        ) from exc

    if value <= 0:
        raise ValueError(
            f"{environment_variable} must be greater than zero"
        )

    return value


async def run_from_environment(
    *,
    stop_event: asyncio.Event,
    run_process: WorkerProcess = run_worker_process,
    artifact_store_factory: ArtifactStoreFactory = build_s3_artifact_store,
) -> None:
    """Load worker configuration and run one worker process."""

    worker_id = resolve_worker_id(os.getenv("WORKER_ID"))

    lease_seconds = read_positive_int(
        "WORKER_LEASE_SECONDS",
        default=DEFAULT_LEASE_SECONDS,
    )
    idle_poll_seconds = read_positive_float(
        "WORKER_IDLE_POLL_SECONDS",
        default=DEFAULT_IDLE_POLL_SECONDS,
    )
    max_response_bytes = read_positive_int(
        "WORKER_MAX_RESPONSE_BYTES",
        default=DEFAULT_MAX_RESPONSE_BYTES,
    )

    artifact_store = artifact_store_factory(get_settings())

    async def persist_document(
        *,
        crawl_run_id: UUID,
        crawl_job_id: UUID,
        artifact: PageArtifact,
    ) -> None:
        async with session_factory() as session:
            existing_document = await session.scalar(
                select(CrawledDocument).where(
                    CrawledDocument.crawl_job_id == crawl_job_id
                )
            )

            if existing_document is not None:
                return

            await persist_crawled_document(
                session=session,
                artifact_store=artifact_store,
                crawl_run_id=crawl_run_id,
                crawl_job_id=crawl_job_id,
                artifact=artifact,
            )
            await session.commit()

    logger.info(
        "Starting crawler worker %s: lease=%ss poll=%.2fs max_bytes=%s",
        worker_id,
        lease_seconds,
        idle_poll_seconds,
        max_response_bytes,
    )

    await run_process(
        worker_id=worker_id,
        lease_seconds=lease_seconds,
        idle_poll_seconds=idle_poll_seconds,
        max_response_bytes=max_response_bytes,
        stop_event=stop_event,
        persist_document=persist_document,
    )


def install_shutdown_handlers(stop_event: asyncio.Event) -> None:
    """
    Convert container SIGTERM and local Ctrl-C into a graceful shutdown.

    The worker loop observes stop_event, exits after its current cycle,
    and marks its heartbeat STOPPED.
    """
    loop = asyncio.get_running_loop()

    def request_shutdown() -> None:
        logger.info("Shutdown requested for crawler worker.")
        stop_event.set()

    for shutdown_signal in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(
                shutdown_signal,
                request_shutdown,
            )
        except (NotImplementedError, RuntimeError):
            signal.signal(
                shutdown_signal,
                lambda *_: request_shutdown(),
            )


async def run_main() -> None:
    stop_event = asyncio.Event()
    install_shutdown_handlers(stop_event)
    await run_from_environment(stop_event=stop_event)


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format=(
            "%(asctime)s %(levelname)s "
            "%(name)s %(message)s"
        ),
    )
    asyncio.run(run_main())


if __name__ == "__main__":
    main()
