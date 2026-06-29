from uuid import UUID

import pytest

from app.crawler.worker_runtime import PageArtifact
from app.models.crawled_document import CrawledDocument


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_persist_crawled_document_uploads_then_records_metadata() -> None:
    from app.crawler.document_persistence import (
        persist_crawled_document,
    )

    class FakeArtifactStore:
        def __init__(self) -> None:
            self.uploaded_artifacts: list[object] = []

        async def put(self, artifact: object) -> None:
            self.uploaded_artifacts.append(artifact)

    class FakeSession:
        def __init__(self) -> None:
            self.added_objects: list[object] = []
            self.flushed = False

        def add(self, instance: object) -> None:
            self.added_objects.append(instance)

        async def flush(self) -> None:
            self.flushed = True

    crawl_run_id = UUID(
        "11111111-1111-1111-1111-111111111111"
    )
    crawl_job_id = UUID(
        "22222222-2222-2222-2222-222222222222"
    )

    artifact_store = FakeArtifactStore()
    session = FakeSession()

    document = await persist_crawled_document(
        session=session,
        artifact_store=artifact_store,
        crawl_run_id=crawl_run_id,
        crawl_job_id=crawl_job_id,
        artifact=PageArtifact(
            content_type="text/html",
            body=(
                b"<html><head>"
                b"<title>OpenRevive page</title>"
                b"</head><body>"
                b"<h1>Research</h1>"
                b"<p>Evidence source.</p>"
                b"</body></html>"
            ),
        ),
    )

    assert len(artifact_store.uploaded_artifacts) == 1

    stored_artifact = artifact_store.uploaded_artifacts[0]
    assert stored_artifact.object_key == (
        "crawl-runs/"
        "11111111-1111-1111-1111-111111111111/"
        "jobs/"
        "22222222-2222-2222-2222-222222222222/"
        "raw.html"
    )
    assert stored_artifact.content_type == "text/html"

    assert isinstance(document, CrawledDocument)
    assert document.crawl_job_id == crawl_job_id
    assert document.raw_object_key == stored_artifact.object_key
    assert document.content_type == "text/html"
    assert document.content_sha256 == stored_artifact.content_sha256
    assert document.title == "OpenRevive page"
    assert document.extracted_text == "Research Evidence source."

    assert session.added_objects == [document]
    assert session.flushed is True


def test_extract_document_content_prefers_article_over_chrome() -> None:
    from app.crawler.document_persistence import (
        extract_document_content,
    )
    from app.crawler.worker_runtime import PageArtifact

    artifact = PageArtifact(
        content_type="text/html",
        body=b"""
        <html>
          <head>
            <title>Coroutines and tasks</title>
          </head>
          <body>
            <div class="bd-header">Theme Auto Light Dark</div>
            <nav>Previous topic Next topic</nav>
            <main>
              <aside>Table of Contents</aside>
              <article>
                <h1>Coroutines and tasks</h1>
                <p>
                  Task groups coordinate concurrent tasks and cancellation.
                </p>
              </article>
              <div class="prev-next-bottom">
                Previous topic Next topic
              </div>
            </main>
            <footer>Report a bug Improve this page</footer>
          </body>
        </html>
        """,
    )

    title, extracted_text = extract_document_content(artifact)

    assert title == "Coroutines and tasks"
    assert extracted_text == (
        "Coroutines and tasks "
        "Task groups coordinate concurrent tasks and cancellation."
    )
    assert "Theme Auto Light Dark" not in extracted_text
    assert "Previous topic" not in extracted_text
    assert "Report a bug" not in extracted_text
