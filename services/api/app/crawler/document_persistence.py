from __future__ import annotations

from collections.abc import Awaitable
from html.parser import HTMLParser
from typing import Protocol
from uuid import UUID

from app.crawler.artifact_storage import (
    StoredPageArtifact,
    describe_page_artifact,
)
from app.crawler.worker_runtime import PageArtifact
from app.models.crawled_document import CrawledDocument


class ArtifactStore(Protocol):
    async def put(
        self,
        artifact: StoredPageArtifact,
    ) -> None: ...


class DocumentSession(Protocol):
    def add(self, instance: object) -> None: ...

    def flush(self) -> Awaitable[None]: ...


class _HTMLContentExtractor(HTMLParser):
    """
    Minimal dependency-free HTML extractor for the hackathon path.

    It keeps visible text, separately captures <title>, and ignores content
    that should not be shown as document text.
    """

    _ignored_tags = {
        "script",
        "style",
        "noscript",
        "template",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._title_parts: list[str] = []
        self._text_parts: list[str] = []
        self._in_title = False
        self._ignored_depth = 0

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        normalized_tag = tag.lower()

        if normalized_tag in self._ignored_tags:
            self._ignored_depth += 1
            return

        if normalized_tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.lower()

        if normalized_tag in self._ignored_tags:
            self._ignored_depth = max(0, self._ignored_depth - 1)
            return

        if normalized_tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._ignored_depth > 0:
            return

        if self._in_title:
            self._title_parts.append(data)
        else:
            self._text_parts.append(data)

    @property
    def title(self) -> str | None:
        return _normalize_text(" ".join(self._title_parts)) or None

    @property
    def extracted_text(self) -> str | None:
        return _normalize_text(" ".join(self._text_parts)) or None


def _normalize_text(value: str) -> str:
    return " ".join(value.split())


def extract_document_content(
    artifact: PageArtifact,
) -> tuple[str | None, str | None]:
    """
    Decode fetched HTML conservatively and produce display/search text.

    Charset-specific decoding can be added later. UTF-8 with replacement is
    sufficient for the current HTML demo path and avoids failing a crawl due
    to one malformed byte sequence.
    """
    parser = _HTMLContentExtractor()
    parser.feed(artifact.body.decode("utf-8", errors="replace"))
    parser.close()

    return parser.title, parser.extracted_text


async def persist_crawled_document(
    *,
    session: DocumentSession,
    artifact_store: ArtifactStore,
    crawl_run_id: UUID,
    crawl_job_id: UUID,
    artifact: PageArtifact,
) -> CrawledDocument:
    """
    Persist one successful fetched page.

    Object storage is written first. If database persistence fails afterwards,
    a retry uses the same deterministic object key and overwrites that object
    rather than creating duplicate raw artifacts.
    """
    stored_artifact = describe_page_artifact(
        crawl_run_id=crawl_run_id,
        crawl_job_id=crawl_job_id,
        artifact=artifact,
    )

    await artifact_store.put(stored_artifact)

    title, extracted_text = extract_document_content(artifact)

    document = CrawledDocument(
        crawl_job_id=crawl_job_id,
        raw_object_key=stored_artifact.object_key,
        content_type=stored_artifact.content_type,
        content_sha256=stored_artifact.content_sha256,
        title=title,
        extracted_text=extracted_text,
    )

    session.add(document)
    await session.flush()

    return document
