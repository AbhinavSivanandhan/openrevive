import pytest

from app.crawler.artifact_storage import StoredPageArtifact


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_minio_artifact_store_uploads_artifact_with_metadata() -> None:
    from app.crawler.minio_artifact_store import MinioArtifactStore

    class FakeS3Client:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def put_object(self, **kwargs: object) -> None:
            self.calls.append(kwargs)

    client = FakeS3Client()
    store = MinioArtifactStore(
        bucket="openrevive-local",
        client=client,
    )

    artifact = StoredPageArtifact(
        object_key=(
            "crawl-runs/run-1/jobs/job-1/raw.html"
        ),
        content_type="text/html",
        content_sha256="a" * 64,
        body=b"<html>OpenRevive</html>",
    )

    await store.put(artifact)

    assert client.calls == [
        {
            "Bucket": "openrevive-local",
            "Key": (
                "crawl-runs/run-1/jobs/job-1/raw.html"
            ),
            "Body": b"<html>OpenRevive</html>",
            "ContentType": "text/html",
            "Metadata": {
                "sha256": "a" * 64,
            },
        }
    ]
