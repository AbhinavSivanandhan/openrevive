import asyncio
from uuid import uuid4

import pytest

from app.crawler.worker_runtime import FetchResult
from app.db.session import session_factory
from app.models.worker_heartbeat import WorkerHeartbeat


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_worker_loop_stops_cleanly_while_idle() -> None:
    from app.crawler.worker_loop import run_worker_loop

    worker_id = f"worker-loop-idle-{uuid4()}"
    stop_event = asyncio.Event()
    sleep_calls = 0

    async def fetch_page(
        url: str,
        timeout_seconds: int,
    ) -> FetchResult:
        raise AssertionError("fetcher must not run with no queued jobs")

    async def sleep(seconds: float) -> None:
        nonlocal sleep_calls
        assert seconds == 0.01
        sleep_calls += 1
        stop_event.set()

    await run_worker_loop(
        worker_id=worker_id,
        lease_seconds=60,
        idle_poll_seconds=0.01,
        fetch_page=fetch_page,
        stop_event=stop_event,
        sleep=sleep,
    )

    assert sleep_calls == 1

    async with session_factory() as session:
        heartbeat = await session.get(
            WorkerHeartbeat,
            worker_id,
        )

    assert heartbeat is not None
    assert heartbeat.status == "STOPPED"
    assert heartbeat.current_job_id is None
    assert heartbeat.stopped_at is not None


@pytest.mark.anyio
async def test_worker_loop_rejects_invalid_runtime_limits() -> None:
    from app.crawler.worker_loop import run_worker_loop

    async def fetch_page(
        url: str,
        timeout_seconds: int,
    ) -> FetchResult:
        raise AssertionError("fetcher must not run")

    with pytest.raises(ValueError, match="lease_seconds"):
        await run_worker_loop(
            worker_id="worker-invalid-lease",
            lease_seconds=0,
            idle_poll_seconds=1,
            fetch_page=fetch_page,
            stop_event=asyncio.Event(),
        )

    with pytest.raises(ValueError, match="idle_poll_seconds"):
        await run_worker_loop(
            worker_id="worker-invalid-poll",
            lease_seconds=60,
            idle_poll_seconds=0,
            fetch_page=fetch_page,
            stop_event=asyncio.Event(),
        )


@pytest.mark.anyio
async def test_worker_loop_registers_once_before_running_cycles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.crawler import worker_loop
    from app.crawler.worker_runtime import WorkerOutcome

    worker_id = f"worker-loop-registration-{uuid4()}"
    stop_event = asyncio.Event()
    registration_flags: list[bool] = []

    async def fake_run_worker_cycle(
        *,
        worker_id: str,
        lease_seconds: int,
        fetch_page,
        persist_document,
        register_worker_if_needed: bool,
    ) -> WorkerOutcome:
        registration_flags.append(register_worker_if_needed)
        stop_event.set()
        return WorkerOutcome(state="IDLE", job_id=None)

    async def fetch_page(
        url: str,
        timeout_seconds: int,
    ) -> FetchResult:
        raise AssertionError("fetcher must not run")

    monkeypatch.setattr(
        worker_loop,
        "run_worker_cycle",
        fake_run_worker_cycle,
    )

    await worker_loop.run_worker_loop(
        worker_id=worker_id,
        lease_seconds=60,
        idle_poll_seconds=0.01,
        fetch_page=fetch_page,
        stop_event=stop_event,
    )

    assert registration_flags == [False]

    async with session_factory() as session:
        heartbeat = await session.get(
            WorkerHeartbeat,
            worker_id,
        )

    assert heartbeat is not None
    assert heartbeat.status == "STOPPED"
    assert heartbeat.stopped_at is not None
