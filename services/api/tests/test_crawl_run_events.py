from uuid import UUID

import pytest

from app.core.config import Settings


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_publish_wakeup_is_a_noop_without_queue_url() -> None:
    from app.crawler.crawl_run_events import publish_crawl_run_wakeup

    def forbidden_client_factory(region_name: str) -> object:
        raise AssertionError("SQS client must not be created")

    published = await publish_crawl_run_wakeup(
        UUID("11111111-1111-1111-1111-111111111111"),
        settings=Settings(crawl_event_queue_url=None),
        client_factory=forbidden_client_factory,
    )

    assert published is False


@pytest.mark.anyio
async def test_publish_wakeup_sends_compact_sqs_event() -> None:
    from app.crawler.crawl_run_events import publish_crawl_run_wakeup

    calls: list[dict[str, str]] = []
    observed_region: list[str] = []

    class FakeSqsClient:
        def send_message(self, **kwargs: str) -> None:
            calls.append(kwargs)

    def client_factory(region_name: str) -> FakeSqsClient:
        observed_region.append(region_name)
        return FakeSqsClient()

    crawl_run_id = UUID("22222222-2222-2222-2222-222222222222")

    published = await publish_crawl_run_wakeup(
        crawl_run_id,
        settings=Settings(
            crawl_event_queue_url=(
                "https://sqs.ap-south-1.amazonaws.com/"
                "123456789012/openrevive-crawl-events"
            ),
            aws_region="ap-south-1",
        ),
        client_factory=client_factory,
    )

    assert published is True
    assert observed_region == ["ap-south-1"]
    assert calls == [
        {
            "QueueUrl": (
                "https://sqs.ap-south-1.amazonaws.com/"
                "123456789012/openrevive-crawl-events"
            ),
            "MessageBody": (
                '{"event_type":"crawl.run.wakeup",'
                '"crawl_run_id":"22222222-2222-2222-2222-222222222222"}'
            ),
        }
    ]
