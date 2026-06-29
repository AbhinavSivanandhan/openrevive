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
    Dependency-free HTML extraction for persisted research documents.

    Prefer article content when available, then main content, and fall back to
    visible text for simpler pages. Navigation and common documentation chrome
    are excluded from every path.
    """

    _ignored_tags = {
        "aside",
        "footer",
        "form",
        "header",
        "nav",
        "noscript",
        "script",
        "style",
        "template",
    }

    _void_tags = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }

    _chrome_fragments = (
        "bd-header",
        "bd-footer",
        "prev-next",
        "search-button",
        "sidebar",
        "skip-link",
        "table-of-contents",
        "theme-switch",
        "toc",
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._title_parts: list[str] = []
        self._fallback_parts: list[str] = []
        self._main_parts: list[str] = []
        self._article_parts: list[str] = []

        self._ignored_depth = 0
        self._main_depth = 0
        self._article_depth = 0
        self._title_depth = 0

        self._tag_stack: list[
            tuple[str, bool, bool, bool, bool]
        ] = []

    @classmethod
    def _is_chrome_container(
        cls,
        attrs: dict[str, str],
    ) -> bool:
        class_and_id = (
            f"{attrs.get('class', '')} {attrs.get('id', '')}"
        ).lower()

        return any(
            fragment in class_and_id
            for fragment in cls._chrome_fragments
        )

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        normalized_tag = tag.lower()

        if normalized_tag in self._void_tags:
            return

        attributes = {
            key.lower(): value or ""
            for key, value in attrs
        }

        ignored = (
            self._ignored_depth > 0
            or normalized_tag in self._ignored_tags
            or self._is_chrome_container(attributes)
        )

        enters_main = (
            not ignored
            and (
                normalized_tag == "main"
                or attributes.get("role", "").lower() == "main"
            )
        )
        enters_article = not ignored and normalized_tag == "article"
        enters_title = not ignored and normalized_tag == "title"

        self._tag_stack.append(
            (
                normalized_tag,
                ignored,
                enters_main,
                enters_article,
                enters_title,
            )
        )

        if ignored:
            self._ignored_depth += 1
            return

        if enters_main:
            self._main_depth += 1

        if enters_article:
            self._article_depth += 1

        if enters_title:
            self._title_depth += 1

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.lower()

        for index in range(
            len(self._tag_stack) - 1,
            -1,
            -1,
        ):
            if self._tag_stack[index][0] != normalized_tag:
                continue

            closing_tags = self._tag_stack[index:]
            del self._tag_stack[index:]

            for (
                _,
                ignored,
                enters_main,
                enters_article,
                enters_title,
            ) in reversed(closing_tags):
                if ignored:
                    self._ignored_depth = max(
                        0,
                        self._ignored_depth - 1,
                    )
                    continue

                if enters_main:
                    self._main_depth = max(
                        0,
                        self._main_depth - 1,
                    )

                if enters_article:
                    self._article_depth = max(
                        0,
                        self._article_depth - 1,
                    )

                if enters_title:
                    self._title_depth = max(
                        0,
                        self._title_depth - 1,
                    )

            return

    def handle_data(self, data: str) -> None:
        if self._ignored_depth > 0:
            return

        if self._title_depth > 0:
            self._title_parts.append(data)
            return

        self._fallback_parts.append(data)

        if self._main_depth > 0:
            self._main_parts.append(data)

        if self._article_depth > 0:
            self._article_parts.append(data)

    @property
    def title(self) -> str | None:
        return _normalize_text(" ".join(self._title_parts)) or None


    @property
    def extracted_text(self) -> str | None:
        for parts in (
            self._article_parts,
            self._main_parts,
            self._fallback_parts,
        ):
            extracted = _normalize_text(" ".join(parts))

            if extracted:
                return extracted

        return None


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
