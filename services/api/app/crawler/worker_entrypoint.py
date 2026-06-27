from __future__ import annotations

import asyncio
import os
import socket
from collections.abc import Awaitable, Callable
from uuid import uuid4

from app.crawler.http_fetcher import HttpxPageFetcher
from app.crawler.worker_loop import run_worker_loop
from app.crawler.worker_runtime import (
    FetchPage,
    PersistDocument,
)

FetcherFactory = Callable[..., HttpxPageFetcher]
WorkerLoop = Callable[..., Awaitable[None]]


def resolve_worker_id(explicit_worker_id: str | None = None) -> str:
    """
    Resolve one identifier for one worker process lifetime.

    Explicit IDs are useful for local demos and deterministic deployments.
    Generated IDs include a random suffix so a restarted process does not
    accidentally look like the prior process in worker_heartbeats.
    """
    if explicit_worker_id is not None:
        normalized_worker_id = explicit_worker_id.strip()

        if not normalized_worker_id:
            raise ValueError("worker_id must not be blank")

        if len(normalized_worker_id) > 128:
            raise ValueError("worker_id must be at most 128 characters")

        return normalized_worker_id

    hostname = socket.gethostname()
    process_id = os.getpid()
    process_suffix = uuid4().hex[:12]

    return f"worker-{hostname}-{process_id}-{process_suffix}"[:128]


async def run_worker_process(
    *,
    worker_id: str,
    lease_seconds: int,
    idle_poll_seconds: float,
    max_response_bytes: int,
    stop_event: asyncio.Event,
    fetcher_factory: FetcherFactory = HttpxPageFetcher,
    run_loop: WorkerLoop = run_worker_loop,
    persist_document: PersistDocument | None = None,
) -> None:
    """
    Own the resources that must live for one worker process lifetime.

    The HTTP fetcher is intentionally created once here rather than once
    per job, allowing its AsyncClient connection pool to be reused.
    """
    if max_response_bytes <= 0:
        raise ValueError("max_response_bytes must be greater than zero")

    async with fetcher_factory(
        max_response_bytes=max_response_bytes,
    ) as fetcher:
        await run_loop(
            worker_id=worker_id,
            lease_seconds=lease_seconds,
            idle_poll_seconds=idle_poll_seconds,
            fetch_page=fetcher,
            stop_event=stop_event,
            persist_document=persist_document,
        )
