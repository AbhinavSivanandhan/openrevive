from __future__ import annotations

import hashlib
from dataclasses import dataclass
from uuid import UUID

from app.crawler.worker_runtime import PageArtifact


@dataclass(frozen=True, slots=True)
class StoredPageArtifact:
    """
    Metadata and bytes ready for object-storage upload.

    The object key is deterministic per crawl job, so retries overwrite the
    same object rather than creating duplicate raw-page artifacts.
    """

    object_key: str
    content_type: str
    content_sha256: str
    body: bytes


def describe_page_artifact(
    *,
    crawl_run_id: UUID,
    crawl_job_id: UUID,
    artifact: PageArtifact,
) -> StoredPageArtifact:
    return StoredPageArtifact(
        object_key=(
            f"crawl-runs/{crawl_run_id}/"
            f"jobs/{crawl_job_id}/raw.html"
        ),
        content_type=artifact.content_type,
        content_sha256=hashlib.sha256(artifact.body).hexdigest(),
        body=artifact.body,
    )
