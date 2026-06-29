import pytest

from app.core.config import Settings
from app.crawler.artifact_storage import StoredPageArtifact


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_s3_artifact_store_uploads_artifact_with_metadata() -> None:
    from app.crawler.minio_artifact_store import S3ArtifactStore

    class FakeS3Client:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def put_object(self, **kwargs: object) -> None:
            self.calls.append(kwargs)

    client = FakeS3Client()
    store = S3ArtifactStore(
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


def test_s3_artifact_store_uses_task_role_mode_without_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.crawler.minio_artifact_store import (
        S3ArtifactStore,
        build_s3_artifact_store,
    )

    observed: dict[str, object] = {}

    class FakeS3Client:
        pass

    def fake_client(
        service_name: str,
        **kwargs: object,
    ) -> FakeS3Client:
        observed["service_name"] = service_name
        observed["kwargs"] = kwargs
        return FakeS3Client()

    monkeypatch.setattr(
        "app.crawler.minio_artifact_store.boto3.client",
        fake_client,
    )

    store = build_s3_artifact_store(
        Settings(
            database_url=(
                "postgresql+asyncpg://test:pass@localhost:5432/"
                "openrevive_test"
            ),
            s3_bucket="openrevive-artifacts",
            s3_region_name="ap-south-1",
        )
    )

    assert isinstance(store, S3ArtifactStore)
    assert observed == {
        "service_name": "s3",
        "kwargs": {
            "region_name": "ap-south-1",
        },
    }
