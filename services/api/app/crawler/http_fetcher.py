from __future__ import annotations

from time import perf_counter

import httpx

from app.crawler.worker_runtime import (
    FetchFailure,
    FetchResult,
    PageArtifact,
)

SUPPORTED_CONTENT_TYPES = {
    "text/html",
    "application/xhtml+xml",
}

DEFAULT_USER_AGENT = "OpenReviveCrawler/0.1"


class HttpxPageFetcher:
    """
    Bounded HTTP fetch adapter owned by one worker process.

    A single AsyncClient is reused for the fetcher's lifetime so the worker
    can reuse HTTP connections across multiple crawl jobs.
    """

    def __init__(
        self,
        *,
        max_response_bytes: int,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be greater than zero")

        self._max_response_bytes = max_response_bytes
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "HttpxPageFetcher":
        await self._get_client()
        return self

    async def __aexit__(
        self,
        exc_type: object,
        exc_value: object,
        traceback: object,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                transport=self._transport,
                follow_redirects=False,
                trust_env=False,
                headers={
                    "Accept": (
                        "text/html,application/xhtml+xml;"
                        "q=0.9,*/*;q=0.1"
                    ),
                    "User-Agent": DEFAULT_USER_AGENT,
                },
            )

        return self._client

    async def __call__(
        self,
        url: str,
        timeout_seconds: int,
    ) -> FetchResult:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")

        started_at = perf_counter()
        timeout = httpx.Timeout(float(timeout_seconds))
        client = await self._get_client()

        try:
            async with client.stream(
                "GET",
                url,
                timeout=timeout,
            ) as response:
                if response.is_redirect:
                    raise FetchFailure(
                        error_code="HTTP_REDIRECT",
                        error_message=(
                            f"redirect response: {response.status_code}"
                        ),
                    )

                if response.status_code < 200 or response.status_code >= 300:
                    raise FetchFailure(
                        error_code=f"HTTP_STATUS_{response.status_code}",
                        error_message=(
                            "non-success HTTP response: "
                            f"{response.status_code}"
                        ),
                    )

                content_type = (
                    response.headers.get("content-type", "")
                    .split(";", 1)[0]
                    .strip()
                    .lower()
                )

                if content_type not in SUPPORTED_CONTENT_TYPES:
                    raise FetchFailure(
                        error_code="UNSUPPORTED_CONTENT_TYPE",
                        error_message=(
                            "expected HTML or XHTML but received "
                            f"{content_type or 'no content type'}"
                        ),
                    )

                content_length = response.headers.get("content-length")

                if content_length is not None:
                    try:
                        declared_bytes = int(content_length)
                    except ValueError:
                        declared_bytes = None

                    if (
                        declared_bytes is not None
                        and declared_bytes > self._max_response_bytes
                    ):
                        raise FetchFailure(
                            error_code="RESPONSE_TOO_LARGE",
                            error_message=(
                                "declared response size exceeds "
                                f"{self._max_response_bytes} bytes"
                            ),
                        )

                fetched_bytes = 0
                body_chunks: list[bytes] = []

                async for chunk in response.aiter_bytes():
                    fetched_bytes += len(chunk)

                    if fetched_bytes > self._max_response_bytes:
                        raise FetchFailure(
                            error_code="RESPONSE_TOO_LARGE",
                            error_message=(
                                "response exceeds "
                                f"{self._max_response_bytes} bytes"
                            ),
                        )

                    body_chunks.append(chunk)

        except FetchFailure:
            raise
        except httpx.TimeoutException as exc:
            raise FetchFailure(
                error_code="HTTP_TIMEOUT",
                error_message="upstream request timed out",
            ) from exc
        except httpx.RequestError as exc:
            raise FetchFailure(
                error_code="HTTP_REQUEST_ERROR",
                error_message=(
                    "HTTP request failed: "
                    f"{exc.__class__.__name__}"
                ),
            ) from exc

        fetch_duration_ms = int((perf_counter() - started_at) * 1_000)

        return FetchResult(
            http_status_code=response.status_code,
            fetched_bytes=fetched_bytes,
            fetch_duration_ms=fetch_duration_ms,
            artifact=PageArtifact(
                content_type=content_type,
                body=b"".join(body_chunks),
            ),
        )
