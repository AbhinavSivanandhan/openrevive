from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any
from uuid import UUID

import boto3

from app.core.config import Settings, get_settings

SqsClientFactory = Callable[[str], Any]


def build_sqs_client(region_name: str) -> Any:
    return boto3.client("sqs", region_name=region_name)


async def publish_crawl_run_wakeup(
    crawl_run_id: UUID,
    *,
    settings: Settings | None = None,
    client_factory: SqsClientFactory = build_sqs_client,
) -> bool:
    """
    Publish one best-effort worker wake-up event after a crawl run is runnable.

    PostgreSQL remains the source of truth for jobs and leases. Duplicate
    queue events are safe: an extra worker finds no claimable job and exits.
    """
    resolved_settings = settings or get_settings()
    queue_url = (resolved_settings.crawl_event_queue_url or "").strip()

    if not queue_url:
        return False

    region_name = (
        (resolved_settings.aws_region or "").strip()
        or resolved_settings.s3_region_name
    )

    message_body = json.dumps(
        {
            "event_type": "crawl.run.wakeup",
            "crawl_run_id": str(crawl_run_id),
        },
        separators=(",", ":"),
    )

    client = client_factory(region_name)

    await asyncio.to_thread(
        client.send_message,
        QueueUrl=queue_url,
        MessageBody=message_body,
    )

    return True
