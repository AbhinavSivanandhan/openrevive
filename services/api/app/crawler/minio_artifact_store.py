from __future__ import annotations

import asyncio
from typing import Any

import boto3
from botocore.config import Config

from app.core.config import Settings
from app.crawler.artifact_storage import StoredPageArtifact


class S3ArtifactStore:
    """
    Async wrapper around an S3-compatible object-storage client.

    boto3 is synchronous, so uploads run in a worker thread rather than
    blocking the crawler event loop.
    """

    def __init__(
        self,
        *,
        bucket: str,
        client: Any,
    ) -> None:
        normalized_bucket = bucket.strip()

        if not normalized_bucket:
            raise ValueError("bucket must not be blank")

        self._bucket = normalized_bucket
        self._client = client

    async def put(
        self,
        artifact: StoredPageArtifact,
    ) -> None:
        await asyncio.to_thread(
            self._client.put_object,
            Bucket=self._bucket,
            Key=artifact.object_key,
            Body=artifact.body,
            ContentType=artifact.content_type,
            Metadata={
                "sha256": artifact.content_sha256,
            },
        )


def build_s3_artifact_store(
    settings: Settings,
) -> S3ArtifactStore:
    """
    Build the artifact store in one of two explicit modes.

    Local mode:
      S3_ENDPOINT_URL + static MinIO credentials + path-style addressing.

    AWS mode:
      No endpoint override. boto3 resolves credentials from the ECS task role.
    """
    bucket = (settings.s3_bucket or "").strip()

    if not bucket:
        raise ValueError(
            "Missing required object-storage setting: S3_BUCKET"
        )

    endpoint_url = (settings.s3_endpoint_url or "").strip()

    if endpoint_url:
        access_key_id = (settings.s3_access_key_id or "").strip()
        secret_access_key = (
            settings.s3_secret_access_key or ""
        ).strip()

        missing = [
            name
            for name, value in {
                "S3_ACCESS_KEY_ID": access_key_id,
                "S3_SECRET_ACCESS_KEY": secret_access_key,
            }.items()
            if not value
        ]

        if missing:
            raise ValueError(
                "Missing required local object-storage settings: "
                + ", ".join(missing)
            )

        client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name=settings.s3_region_name,
            config=Config(
                s3={
                    "addressing_style": "path",
                }
            ),
        )
    else:
        client = boto3.client(
            "s3",
            region_name=settings.s3_region_name,
        )

    return S3ArtifactStore(
        bucket=bucket,
        client=client,
    )


MinioArtifactStore = S3ArtifactStore
build_minio_artifact_store = build_s3_artifact_store
