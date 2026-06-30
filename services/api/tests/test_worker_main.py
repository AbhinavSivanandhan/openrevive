import asyncio

import pytest


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_run_from_environment_delegates_worker_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.crawler.worker_main import run_from_environment

    monkeypatch.setenv("WORKER_ID", "crawler-demo-a")
    monkeypatch.setenv("WORKER_LEASE_SECONDS", "45")
    monkeypatch.setenv("WORKER_IDLE_POLL_SECONDS", "1.25")
    monkeypatch.setenv("WORKER_MAX_RESPONSE_BYTES", "2500000")

    observed: dict[str, object] = {}
    stop_event = asyncio.Event()
    fake_artifact_store = object()

    def fake_artifact_store_factory(settings: object) -> object:
        observed["settings"] = settings
        return fake_artifact_store

    async def fake_run_worker_process(**kwargs: object) -> None:
        observed.update(kwargs)

    await run_from_environment(
        stop_event=stop_event,
        run_process=fake_run_worker_process,
        artifact_store_factory=fake_artifact_store_factory,
    )

    assert observed["worker_id"] == "crawler-demo-a"
    assert observed["lease_seconds"] == 45
    assert observed["idle_poll_seconds"] == 1.25
    assert observed["max_response_bytes"] == 2_500_000
    assert observed["exit_when_idle"] is False
    assert observed["idle_polls_before_exit"] == 2
    assert observed["stop_event"] is stop_event
    assert observed["settings"] is not None
    assert callable(observed["persist_document"])


@pytest.mark.anyio
async def test_run_from_environment_rejects_invalid_runtime_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.crawler.worker_main import run_from_environment

    monkeypatch.setenv("WORKER_LEASE_SECONDS", "0")

    async def fake_run_worker_process(**kwargs: object) -> None:
        raise AssertionError("worker process must not start")

    with pytest.raises(
        ValueError,
        match="WORKER_LEASE_SECONDS",
    ):
        await run_from_environment(
            stop_event=asyncio.Event(),
            run_process=fake_run_worker_process,
        )
