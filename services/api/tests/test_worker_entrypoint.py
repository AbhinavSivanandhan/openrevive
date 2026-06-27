import asyncio

import pytest


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_resolve_worker_id_preserves_explicit_override() -> None:
    from app.crawler.worker_entrypoint import resolve_worker_id

    assert resolve_worker_id("crawler-demo-a") == "crawler-demo-a"


@pytest.mark.anyio
async def test_run_worker_process_owns_one_fetcher_for_entire_loop() -> None:
    from app.crawler.worker_entrypoint import run_worker_process

    observed: dict[str, object] = {}

    class FakeFetcher:
        def __init__(self) -> None:
            self.closed = False

        async def __aenter__(self) -> "FakeFetcher":
            observed["entered"] = True
            return self

        async def __aexit__(
            self,
            exc_type: object,
            exc_value: object,
            traceback: object,
        ) -> None:
            self.closed = True
            observed["exited"] = True

    fake_fetcher = FakeFetcher()
    stop_event = asyncio.Event()

    def fetcher_factory(
        *,
        max_response_bytes: int,
    ) -> FakeFetcher:
        observed["max_response_bytes"] = max_response_bytes
        return fake_fetcher

    async def fake_run_worker_loop(
        *,
        worker_id: str,
        lease_seconds: int,
        idle_poll_seconds: float,
        fetch_page: object,
        stop_event: asyncio.Event,
        persist_document: object | None,
    ) -> None:
        observed["worker_id"] = worker_id
        observed["lease_seconds"] = lease_seconds
        observed["idle_poll_seconds"] = idle_poll_seconds
        observed["fetch_page"] = fetch_page
        observed["stop_event"] = stop_event
        observed["persist_document"] = persist_document
        assert fake_fetcher.closed is False

    await run_worker_process(
        worker_id="crawler-demo-a",
        lease_seconds=60,
        idle_poll_seconds=1.5,
        max_response_bytes=2_000_000,
        stop_event=stop_event,
        fetcher_factory=fetcher_factory,
        run_loop=fake_run_worker_loop,
    )

    assert observed == {
        "entered": True,
        "max_response_bytes": 2_000_000,
        "worker_id": "crawler-demo-a",
        "lease_seconds": 60,
        "idle_poll_seconds": 1.5,
        "fetch_page": fake_fetcher,
        "stop_event": stop_event,
        "persist_document": None,
        "exited": True,
    }
    assert fake_fetcher.closed is True
