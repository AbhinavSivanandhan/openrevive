import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.briefing.bedrock_brief_generator import (
    BriefGenerationError,
    GeneratedCampaignBrief,
)
from app.db.session import session_factory
from app.main import app
from app.models.campaign_brief import CampaignBrief
from app.models.collection import Collection
from app.models.crawled_document import CrawledDocument
from app.models.crawl_job import CrawlJob
from app.models.crawl_run import CrawlRun
from app.models.workspace import Workspace


async def create_completed_campaign() -> tuple[UUID, UUID, UUID]:
    async with session_factory() as session:
        workspace = Workspace(
            name=f"Brief Route Workspace {uuid4()}",
        )
        session.add(workspace)
        await session.flush()

        collection = Collection(
            workspace_id=workspace.id,
            name=f"Brief Route Collection {uuid4()}",
        )
        session.add(collection)
        await session.flush()

        now = datetime.now(UTC)

        crawl_run = CrawlRun(
            collection_id=collection.id,
            status="SUCCEEDED",
            name="Distributed crawler campaign",
            research_intent=(
                "Explain durable worker leases and crawl pacing."
            ),
            seed_urls=["https://docs.example.com/leases"],
            allowed_domains=["docs.example.com"],
            max_pages=10,
            max_depth=1,
            request_timeout_seconds=15,
            max_attempts=3,
            idempotency_key=str(uuid4()),
            started_at=now,
            completed_at=now,
        )
        session.add(crawl_run)
        await session.flush()

        crawl_job = CrawlJob(
            crawl_run_id=crawl_run.id,
            original_url="https://docs.example.com/leases",
            normalized_url="https://docs.example.com/leases",
            domain="docs.example.com",
            depth=0,
            status="SUCCEEDED",
            max_attempts=3,
            finished_at=now,
        )
        session.add(crawl_job)
        await session.flush()

        document = CrawledDocument(
            crawl_job_id=crawl_job.id,
            raw_object_key=(
                f"crawl-runs/{crawl_run.id}/"
                f"jobs/{crawl_job.id}/raw.html"
            ),
            content_type="text/html",
            content_sha256="a" * 64,
            title="Durable worker leases",
            extracted_text=(
                "PostgreSQL leases coordinate distributed crawler "
                "workers and prevent duplicate processing."
            ),
        )
        session.add(document)

        await session.commit()

        return collection.id, crawl_run.id, document.id


def test_campaign_brief_endpoint_generates_once_and_returns_cache(
    monkeypatch,
) -> None:
    collection_id, crawl_run_id, document_id = asyncio.run(
        create_completed_campaign()
    )

    calls: list[object] = []

    async def fake_generate_campaign_brief(**kwargs: object):
        calls.append(kwargs)

        return GeneratedCampaignBrief(
            brief_json={
                "overview": "Workers use durable leases.",
                "key_findings": [
                    {
                        "statement": (
                            "Leases prevent duplicate processing."
                        ),
                        "source_document_ids": [str(document_id)],
                    }
                ],
                "open_questions": [],
                "recommended_follow_ups": [
                    "Measure lease contention."
                ],
            },
            output_token_count=41,
        )

    monkeypatch.setattr(
        "app.api.routers.crawl_runs.generate_campaign_brief",
        fake_generate_campaign_brief,
    )

    route = (
        f"/v1/collections/{collection_id}/crawl-runs/"
        f"{crawl_run_id}/brief"
    )

    with TestClient(app) as client:
        first_response = client.post(route)
        second_response = client.post(route)

    assert first_response.status_code == 200
    assert second_response.status_code == 200

    first = first_response.json()
    second = second_response.json()

    assert first["status"] == "READY"
    assert second["status"] == "READY"
    assert first["id"] == second["id"]
    assert first["output_token_count"] == 41
    assert len(calls) == 1


def test_failed_campaign_brief_retries_only_on_a_later_post(
    monkeypatch,
) -> None:
    collection_id, crawl_run_id, document_id = asyncio.run(
        create_completed_campaign()
    )

    calls: list[object] = []

    async def fake_generate_campaign_brief(**kwargs: object):
        calls.append(kwargs)

        if len(calls) == 1:
            raise BriefGenerationError("temporary provider failure")

        return GeneratedCampaignBrief(
            brief_json={
                "overview": "Retry succeeded.",
                "key_findings": [
                    {
                        "statement": "The evidence was processed.",
                        "source_document_ids": [str(document_id)],
                    }
                ],
                "open_questions": [],
                "recommended_follow_ups": [],
            },
            output_token_count=22,
        )

    monkeypatch.setattr(
        "app.api.routers.crawl_runs.generate_campaign_brief",
        fake_generate_campaign_brief,
    )

    route = (
        f"/v1/collections/{collection_id}/crawl-runs/"
        f"{crawl_run_id}/brief"
    )

    with TestClient(app) as client:
        failed_response = client.post(route)
        retried_response = client.post(route)

    assert failed_response.status_code == 200
    assert failed_response.json()["status"] == "FAILED"
    assert failed_response.json()["error_code"] == (
        "BEDROCK_GENERATION_FAILED"
    )

    assert retried_response.status_code == 200
    assert retried_response.json()["status"] == "READY"
    assert len(calls) == 2

    async def count_briefs() -> int:
        async with session_factory() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(CampaignBrief)
                .where(CampaignBrief.crawl_run_id == crawl_run_id)
            )
            return int(count or 0)

    assert asyncio.run(count_briefs()) == 1
