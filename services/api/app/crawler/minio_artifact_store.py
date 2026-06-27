from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import boto3
from botocore.config import Config

from app.core.config import Settings
from app.crawler.artifact_storage import StoredPageArtifact


class MinioArtifactStore:
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


def build_minio_artifact_store(
    settings: Settings,
) -> MinioArtifactStore:
    required_values = {
        "S3_ENDPOINT_URL": settings.s3_endpoint_url,
        "S3_BUCKET": settings.s3_bucket,
        "S3_ACCESS_KEY_ID": settings.s3_access_key_id,
        "S3_SECRET_ACCESS_KEY": settings.s3_secret_access_key,
    }

    missing = [
        name
        for name, value in required_values.items()
        if value is None or not value.strip()
    ]

    if missing:
        joined = ", ".join(missing)
        raise ValueError(
            f"Missing required object-storage settings: {joined}"
        )

    client = boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key_id,
        aws_secret_access_key=settings.s3_secret_access_key,
        region_name=settings.s3_region_name,
        config=Config(
            s3={
                "addressing_style": "path",
            }
        ),
    )

    return MinioArtifactStore(
        bucket=settings.s3_bucket,
        client=client,
    )
