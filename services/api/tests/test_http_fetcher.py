import httpx
import pytest

from app.crawler.worker_runtime import FetchFailure


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_fetcher_returns_metrics_for_html_response() -> None:
    from app.crawler.http_fetcher import HttpxPageFetcher

    body = b"<html><body>OpenRevive</body></html>"
    observed: dict[str, str] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        observed["method"] = request.method
        observed["accept"] = request.headers["accept"]
        observed["user_agent"] = request.headers["user-agent"]

        return httpx.Response(
            200,
            headers={
                "content-type": "text/html; charset=utf-8",
            },
            content=body,
        )

    fetcher = HttpxPageFetcher(
        transport=httpx.MockTransport(handler),
        max_response_bytes=1_024,
    )

    result = await fetcher(
        "https://example.com/page",
        timeout_seconds=15,
    )

    assert result.http_status_code == 200
    assert result.fetched_bytes == len(body)
    assert result.fetch_duration_ms >= 0
    assert observed["method"] == "GET"
    assert "text/html" in observed["accept"]
    assert observed["user_agent"].startswith("OpenReviveCrawler/")


@pytest.mark.anyio
async def test_fetcher_rejects_redirects() -> None:
    from app.crawler.http_fetcher import HttpxPageFetcher

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            headers={"location": "https://example.com/other"},
        )

    fetcher = HttpxPageFetcher(
        transport=httpx.MockTransport(handler),
        max_response_bytes=1_024,
    )

    with pytest.raises(FetchFailure) as error:
        await fetcher(
            "https://example.com/redirect",
            timeout_seconds=15,
        )

    assert error.value.error_code == "HTTP_REDIRECT"


@pytest.mark.anyio
async def test_fetcher_rejects_non_html_content() -> None:
    from app.crawler.http_fetcher import HttpxPageFetcher

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/pdf"},
            content=b"%PDF-1.7",
        )

    fetcher = HttpxPageFetcher(
        transport=httpx.MockTransport(handler),
        max_response_bytes=1_024,
    )

    with pytest.raises(FetchFailure) as error:
        await fetcher(
            "https://example.com/document.pdf",
            timeout_seconds=15,
        )

    assert error.value.error_code == "UNSUPPORTED_CONTENT_TYPE"


@pytest.mark.anyio
async def test_fetcher_rejects_responses_over_byte_budget() -> None:
    from app.crawler.http_fetcher import HttpxPageFetcher

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=b"x" * 1_025,
        )

    fetcher = HttpxPageFetcher(
        transport=httpx.MockTransport(handler),
        max_response_bytes=1_024,
    )

    with pytest.raises(FetchFailure) as error:
        await fetcher(
            "https://example.com/large",
            timeout_seconds=15,
        )

    assert error.value.error_code == "RESPONSE_TOO_LARGE"


@pytest.mark.anyio
async def test_fetcher_maps_network_timeout_to_fetch_failure() -> None:
    from app.crawler.http_fetcher import HttpxPageFetcher

    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout(
            "upstream request timed out",
            request=request,
        )

    fetcher = HttpxPageFetcher(
        transport=httpx.MockTransport(handler),
        max_response_bytes=1_024,
    )

    with pytest.raises(FetchFailure) as error:
        await fetcher(
            "https://example.com/timeout",
            timeout_seconds=15,
        )

    assert error.value.error_code == "HTTP_TIMEOUT"


@pytest.mark.anyio
async def test_fetcher_reuses_one_client_until_it_is_closed() -> None:
    from app.crawler.http_fetcher import HttpxPageFetcher

    request_count = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1

        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=b"<html><body>OpenRevive</body></html>",
        )

    fetcher = HttpxPageFetcher(
        transport=httpx.MockTransport(handler),
        max_response_bytes=1_024,
    )

    async with fetcher:
        await fetcher(
            "https://example.com/first",
            timeout_seconds=15,
        )

        first_client = fetcher._client
        assert first_client is not None

        await fetcher(
            "https://example.com/second",
            timeout_seconds=15,
        )

        assert fetcher._client is first_client
        assert first_client.is_closed is False

    assert request_count == 2
    assert first_client.is_closed is True


@pytest.mark.anyio
async def test_fetcher_returns_bounded_page_artifact() -> None:
    from app.crawler.http_fetcher import HttpxPageFetcher

    body = b"<html><head><title>OpenRevive</title></head><body>crawler</body></html>"

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=body,
        )

    fetcher = HttpxPageFetcher(
        transport=httpx.MockTransport(handler),
        max_response_bytes=1_024,
    )

    async with fetcher:
        result = await fetcher(
            "https://example.com/article",
            timeout_seconds=15,
        )

    assert result.http_status_code == 200
    assert result.fetched_bytes == len(body)
    assert result.artifact.content_type == "text/html"
    assert result.artifact.body == body
