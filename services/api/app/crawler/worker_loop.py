from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from app.crawler.worker_heartbeats import (
    register_worker,
    stop_worker,
)
from app.crawler.worker_runtime import (
    FetchPage,
    PersistDocument,
)
from app.crawler.worker_service import run_worker_cycle
from app.db.session import session_factory

Sleep = Callable[[float], Awaitable[None]]


async def run_worker_loop(
    *,
    worker_id: str,
    lease_seconds: int,
    idle_poll_seconds: float,
    fetch_page: FetchPage,
    stop_event: asyncio.Event,
    persist_document: PersistDocument | None = None,
    sleep: Sleep = asyncio.sleep,
) -> None:
    """
    Run observable worker cycles until graceful shutdown is requested.

    A worker sleeps only after an idle cycle. When jobs are available it
    immediately starts another cycle, allowing one worker to drain work
    without artificial delay.
    """
    if lease_seconds <= 0:
        raise ValueError("lease_seconds must be greater than zero")

    if idle_poll_seconds <= 0:
        raise ValueError("idle_poll_seconds must be greater than zero")

    async with session_factory() as session:
        await register_worker(
            session,
            worker_id=worker_id,
        )

    try:
        while not stop_event.is_set():
            outcome = await run_worker_cycle(
                worker_id=worker_id,
                lease_seconds=lease_seconds,
                fetch_page=fetch_page,
                persist_document=persist_document,
                register_worker_if_needed=False,
            )

            if outcome.state == "IDLE" and not stop_event.is_set():
                await sleep(idle_poll_seconds)
    finally:
        async with session_factory() as session:
            await stop_worker(
                session,
                worker_id=worker_id,
            )
