import hashlib
from uuid import UUID

from app.crawler.worker_runtime import PageArtifact


def test_describe_page_artifact_builds_retry_safe_storage_metadata() -> None:
    from app.crawler.artifact_storage import describe_page_artifact

    crawl_run_id = UUID("11111111-1111-1111-1111-111111111111")
    crawl_job_id = UUID("22222222-2222-2222-2222-222222222222")
    body = b"<html><body>OpenRevive</body></html>"

    stored_artifact = describe_page_artifact(
        crawl_run_id=crawl_run_id,
        crawl_job_id=crawl_job_id,
        artifact=PageArtifact(
            content_type="text/html",
            body=body,
        ),
    )

    assert stored_artifact.object_key == (
        "crawl-runs/"
        "11111111-1111-1111-1111-111111111111/"
        "jobs/"
        "22222222-2222-2222-2222-222222222222/"
        "raw.html"
    )
    assert stored_artifact.content_type == "text/html"
    assert stored_artifact.content_sha256 == hashlib.sha256(
        body
    ).hexdigest()
