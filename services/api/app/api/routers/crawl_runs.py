from __future__ import annotations

from datetime import datetime
from typing import Annotated
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.crawler.crawl_run_events import publish_crawl_run_wakeup
from app.crawler.frontier_discovery import normalize_discovered_url

from app.briefing.bedrock_brief_generator import (
    BriefGenerationError,
    generate_campaign_brief_from_plan,
)
from app.briefing.campaign_brief_service import (
    NoUsableCampaignEvidenceError,
    claim_failed_campaign_brief_for_retry,
    mark_campaign_brief_failed,
    mark_campaign_brief_ready,
    reserve_campaign_brief,
)
from app.core.config import get_settings
from app.models.campaign_brief import CampaignBrief
from app.db.session import get_db_session
from app.models.collection import Collection
from app.models.crawled_document import CrawledDocument
from app.models.crawl_job import CrawlJob
from app.models.crawl_run import CrawlRun

router = APIRouter(
    prefix="/v1/collections/{collection_id}/crawl-runs",
    tags=["crawl-runs"],
)

IdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=128,
    ),
]


class CrawlRunCreateRequest(BaseModel):
    seed_urls: list[str] = Field(min_length=1, max_length=100)
    allowed_domains: list[str] = Field(min_length=1, max_length=100)
    research_intent: str | None = Field(default=None, max_length=500)
    name: str | None = Field(default=None, max_length=160)
    max_pages: int = Field(gt=0, le=10_000)
    max_depth: int = Field(ge=0, le=20)
    request_timeout_seconds: int = Field(gt=0, le=120)
    max_attempts: int = Field(gt=0, le=10)

    @field_validator("research_intent")
    @classmethod
    def normalize_research_intent(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None

        normalized_value = value.strip()

        if not normalized_value:
            raise ValueError(
                "research_intent must not be blank"
            )

        return normalized_value

    @field_validator("name")
    @classmethod
    def normalize_name(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None

        normalized_value = value.strip()

        if not normalized_value:
            raise ValueError("name must not be blank")

        return normalized_value


class CrawlRunResponse(BaseModel):
    id: UUID
    collection_id: UUID
    status: str
    name: str | None
    seed_urls: list[str]
    allowed_domains: list[str]
    research_intent: str | None
    max_pages: int
    max_depth: int
    request_timeout_seconds: int
    max_attempts: int
    queued_job_count: int
    created_at: datetime


class CrawlRunDetailResponse(BaseModel):
    id: UUID
    collection_id: UUID
    status: str
    name: str | None
    research_intent: str | None
    job_counts: dict[str, int]
    created_at: datetime


class CampaignBriefResponse(BaseModel):
    id: UUID
    crawl_run_id: UUID
    status: str
    model_id: str
    prompt_version: str
    input_document_count: int
    input_character_count: int
    output_token_count: int | None
    brief_json: dict[str, object] | None
    error_code: str | None
    completed_at: datetime | None
    created_at: datetime


def to_campaign_brief_response(
    brief: CampaignBrief,
) -> CampaignBriefResponse:
    return CampaignBriefResponse(
        id=brief.id,
        crawl_run_id=brief.crawl_run_id,
        status=brief.status,
        model_id=brief.model_id,
        prompt_version=brief.prompt_version,
        input_document_count=brief.input_document_count,
        input_character_count=brief.input_character_count,
        output_token_count=brief.output_token_count,
        brief_json=brief.brief_json,
        error_code=brief.error_code,
        completed_at=brief.completed_at,
        created_at=brief.created_at,
    )


class CrawledDocumentResponse(BaseModel):
    id: UUID
    crawl_job_id: UUID
    source_url: str
    title: str | None
    extracted_text_preview: str | None
    raw_object_key: str
    content_type: str
    created_at: datetime


class CrawledDocumentDetailResponse(BaseModel):
    id: UUID
    crawl_job_id: UUID
    source_url: str
    original_url: str
    title: str | None
    extracted_text: str | None
    raw_object_key: str
    content_type: str
    created_at: datetime


class CrawledDocumentListResponse(BaseModel):
    total: int
    items: list[CrawledDocumentResponse]


class CrawlFrontierJobResponse(BaseModel):
    id: UUID
    parent_job_id: UUID | None
    parent_url: str | None
    original_url: str
    normalized_url: str
    domain: str
    depth: int
    anchor_text: str | None
    priority_score: int
    priority_band: str
    discovery_reason: str | None
    status: str
    attempt_count: int
    max_attempts: int
    last_claimed_by_worker_id: str | None
    last_error_code: str | None
    last_error_message: str | None
    http_status_code: int | None
    fetched_bytes: int | None
    fetch_duration_ms: int | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


class CrawlFrontierListResponse(BaseModel):
    total: int
    items: list[CrawlFrontierJobResponse]

JOB_COUNT_STATUSES = (
    "PENDING",
    "RETRY_PENDING",
    "LEASED",
    "SUCCEEDED",
    "FAILED",
    "SKIPPED",
    "CANCELLED",
)


class CrawlRunSummaryResponse(BaseModel):
    id: UUID
    collection_id: UUID
    status: str
    name: str | None
    seed_urls: list[str]
    research_intent: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    job_counts: dict[str, int]


class CrawlRunListResponse(BaseModel):
    total: int
    items: list[CrawlRunSummaryResponse]


def normalize_allowed_domain(value: str) -> str:
    domain = value.strip().lower().rstrip(".")

    if (
        not domain
        or "://" in domain
        or "/" in domain
        or "@" in domain
        or ":" in domain
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"invalid allowed domain: {value!r}",
        )

    return domain


def normalize_seed_url(value: str) -> tuple[str, str]:
    raw_url = value.strip()

    try:
        parsed = urlsplit(raw_url)
        _ = parsed.port
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"invalid seed URL: {value!r}",
        ) from exc

    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower().rstrip(".")

    if scheme not in {"http", "https"}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="seed URLs must use http or https",
        )

    if not host or parsed.username or parsed.password:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"invalid seed URL: {value!r}",
        )

    normalized_url = normalize_discovered_url(
        base_url=raw_url,
        href=raw_url,
        allowed_domains=[host],
    )

    if normalized_url is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"invalid seed URL: {value!r}",
        )

    return normalized_url, host


def domain_is_allowed(domain: str, allowed_domains: list[str]) -> bool:
    return any(
        domain == allowed_domain
        or domain.endswith(f".{allowed_domain}")
        for allowed_domain in allowed_domains
    )


def same_request(
    crawl_run: CrawlRun,
    *,
    seed_urls: list[str],
    allowed_domains: list[str],
    research_intent: str | None,
    payload: CrawlRunCreateRequest,
) -> bool:
    return (
        crawl_run.name == payload.name
        and crawl_run.seed_urls == seed_urls
        and crawl_run.allowed_domains == allowed_domains
        and crawl_run.research_intent == research_intent
        and crawl_run.max_pages == payload.max_pages
        and crawl_run.max_depth == payload.max_depth
        and crawl_run.request_timeout_seconds
        == payload.request_timeout_seconds
        and crawl_run.max_attempts == payload.max_attempts
    )


async def queued_job_count(
    session: AsyncSession,
    crawl_run_id: UUID,
) -> int:
    count = await session.scalar(
        select(func.count())
        .select_from(CrawlJob)
        .where(CrawlJob.crawl_run_id == crawl_run_id)
    )
    return int(count or 0)


async def to_response(
    session: AsyncSession,
    crawl_run: CrawlRun,
) -> CrawlRunResponse:
    return CrawlRunResponse(
        id=crawl_run.id,
        collection_id=crawl_run.collection_id,
        status=crawl_run.status,
        name=crawl_run.name,
        seed_urls=crawl_run.seed_urls,
        allowed_domains=crawl_run.allowed_domains,
        research_intent=crawl_run.research_intent,
        max_pages=crawl_run.max_pages,
        max_depth=crawl_run.max_depth,
        request_timeout_seconds=crawl_run.request_timeout_seconds,
        max_attempts=crawl_run.max_attempts,
        queued_job_count=await queued_job_count(session, crawl_run.id),
        created_at=crawl_run.created_at,
    )


async def get_crawl_run_for_collection(
    session: AsyncSession,
    *,
    collection_id: UUID,
    crawl_run_id: UUID,
) -> CrawlRun:
    crawl_run = await session.scalar(
        select(CrawlRun).where(
            CrawlRun.id == crawl_run_id,
            CrawlRun.collection_id == collection_id,
        )
    )

    if crawl_run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="crawl run not found",
        )

    return crawl_run


async def get_job_counts(
    session: AsyncSession,
    crawl_run_id: UUID,
) -> dict[str, int]:
    rows = (
        await session.execute(
            select(
                CrawlJob.status,
                func.count(),
            )
            .where(CrawlJob.crawl_run_id == crawl_run_id)
            .group_by(CrawlJob.status)
        )
    ).all()

    job_counts = {
        status_value: 0
        for status_value in JOB_COUNT_STATUSES
    }

    for status_value, count in rows:
        job_counts[status_value] = int(count)

    job_counts["TOTAL"] = sum(job_counts.values())

    return job_counts


async def get_crawl_run_for_update(
    session: AsyncSession,
    *,
    collection_id: UUID,
    crawl_run_id: UUID,
) -> CrawlRun:
    crawl_run = await session.scalar(
        select(CrawlRun)
        .where(
            CrawlRun.id == crawl_run_id,
            CrawlRun.collection_id == collection_id,
        )
        .with_for_update()
    )

    if crawl_run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="crawl run not found",
        )

    return crawl_run


async def to_detail_response(
    session: AsyncSession,
    crawl_run: CrawlRun,
) -> CrawlRunDetailResponse:
    return CrawlRunDetailResponse(
        id=crawl_run.id,
        collection_id=crawl_run.collection_id,
        status=crawl_run.status,
        name=crawl_run.name,
        research_intent=crawl_run.research_intent,
        job_counts=await get_job_counts(session, crawl_run.id),
        created_at=crawl_run.created_at,
    )


async def reconcile_paused_campaign_on_resume(
    session: AsyncSession,
    *,
    crawl_run: CrawlRun,
    database_now: datetime,
) -> None:
    """
    Resume a paused campaign safely.

    A worker may have finished its final leased job while the campaign was
    paused. In that case there is nothing left to resume, so derive the
    terminal campaign state instead of leaving it RUNNING forever.
    """
    job_counts = await get_job_counts(session, crawl_run.id)

    terminal_job_count = sum(
        job_counts[status_value]
        for status_value in (
            "SUCCEEDED",
            "FAILED",
            "SKIPPED",
            "CANCELLED",
        )
    )

    if (
        job_counts["TOTAL"] > 0
        and terminal_job_count == job_counts["TOTAL"]
    ):
        if job_counts["FAILED"] > 0:
            crawl_run.status = (
                "PARTIALLY_SUCCEEDED"
                if job_counts["SUCCEEDED"] > 0
                else "FAILED"
            )
        else:
            crawl_run.status = "SUCCEEDED"

        crawl_run.completed_at = database_now
    else:
        crawl_run.status = "RUNNING"
        crawl_run.completed_at = None

    crawl_run.updated_at = database_now


async def publish_wakeup_if_running(
    crawl_run: CrawlRun,
) -> None:
    if crawl_run.status != "RUNNING":
        return

    try:
        await publish_crawl_run_wakeup(crawl_run.id)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "crawl run is RUNNING, but its worker wake-up signal "
                "could not be sent; retry start or resume"
            ),
        ) from exc


@router.post(
    "/{crawl_run_id}/start",
    response_model=CrawlRunDetailResponse,
)
async def start_crawl_run(
    collection_id: UUID,
    crawl_run_id: UUID,
    session: AsyncSession = Depends(get_db_session),
) -> CrawlRunDetailResponse:
    async with session.begin():
        crawl_run = await get_crawl_run_for_update(
            session,
            collection_id=collection_id,
            crawl_run_id=crawl_run_id,
        )

        if crawl_run.status == "PENDING":
            database_now = await session.scalar(select(func.now()))
            crawl_run.status = "RUNNING"
            crawl_run.started_at = crawl_run.started_at or database_now
            crawl_run.updated_at = database_now
        elif crawl_run.status != "RUNNING":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "only a PENDING campaign can be started; "
                    f"current status is {crawl_run.status}"
                ),
            )

    await publish_wakeup_if_running(crawl_run)

    return await to_detail_response(session, crawl_run)


@router.post(
    "/{crawl_run_id}/pause",
    response_model=CrawlRunDetailResponse,
)
async def pause_crawl_run(
    collection_id: UUID,
    crawl_run_id: UUID,
    session: AsyncSession = Depends(get_db_session),
) -> CrawlRunDetailResponse:
    async with session.begin():
        crawl_run = await get_crawl_run_for_update(
            session,
            collection_id=collection_id,
            crawl_run_id=crawl_run_id,
        )

        if crawl_run.status == "RUNNING":
            crawl_run.status = "PAUSED"
            crawl_run.updated_at = await session.scalar(select(func.now()))
        elif crawl_run.status != "PAUSED":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "only a RUNNING campaign can be paused; "
                    f"current status is {crawl_run.status}"
                ),
            )

    return await to_detail_response(session, crawl_run)


@router.post(
    "/{crawl_run_id}/resume",
    response_model=CrawlRunDetailResponse,
)
async def resume_crawl_run(
    collection_id: UUID,
    crawl_run_id: UUID,
    session: AsyncSession = Depends(get_db_session),
) -> CrawlRunDetailResponse:
    async with session.begin():
        crawl_run = await get_crawl_run_for_update(
            session,
            collection_id=collection_id,
            crawl_run_id=crawl_run_id,
        )

        if crawl_run.status == "PAUSED":
            database_now = await session.scalar(select(func.now()))

            if not isinstance(database_now, datetime):
                raise RuntimeError(
                    "database did not return a timestamp"
                )

            await reconcile_paused_campaign_on_resume(
                session,
                crawl_run=crawl_run,
                database_now=database_now,
            )
        elif crawl_run.status != "RUNNING":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "only a PAUSED campaign can be resumed; "
                    f"current status is {crawl_run.status}"
                ),
            )

    await publish_wakeup_if_running(crawl_run)

    return await to_detail_response(session, crawl_run)


@router.post(
    "/{crawl_run_id}/cancel",
    response_model=CrawlRunDetailResponse,
)
async def cancel_crawl_run(
    collection_id: UUID,
    crawl_run_id: UUID,
    session: AsyncSession = Depends(get_db_session),
) -> CrawlRunDetailResponse:
    async with session.begin():
        crawl_run = await get_crawl_run_for_update(
            session,
            collection_id=collection_id,
            crawl_run_id=crawl_run_id,
        )

        if crawl_run.status == "CANCELLED":
            pass
        elif crawl_run.status in {"PENDING", "RUNNING", "PAUSED"}:
            database_now = await session.scalar(select(func.now()))

            crawl_run.status = "CANCELLED"
            crawl_run.updated_at = database_now

            await session.execute(
                update(CrawlJob)
                .where(
                    CrawlJob.crawl_run_id == crawl_run.id,
                    CrawlJob.status.in_(
                        ["PENDING", "RETRY_PENDING"],
                    ),
                )
                .values(
                    status="CANCELLED",
                    lease_owner=None,
                    lease_token=None,
                    lease_expires_at=None,
                    last_error_code="CAMPAIGN_CANCELLED",
                    last_error_message=(
                        "campaign cancelled by control plane"
                    ),
                    finished_at=database_now,
                    updated_at=database_now,
                )
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "completed campaigns cannot be cancelled; "
                    f"current status is {crawl_run.status}"
                ),
            )

    return await to_detail_response(session, crawl_run)


@router.get(
    "",
    response_model=CrawlRunListResponse,
)
async def list_crawl_runs(
    collection_id: UUID,
    session: AsyncSession = Depends(get_db_session),
) -> CrawlRunListResponse:
    collection = await session.get(Collection, collection_id)

    if collection is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="collection not found",
        )

    crawl_runs = list(
        await session.scalars(
            select(CrawlRun)
            .where(CrawlRun.collection_id == collection_id)
            .order_by(
                CrawlRun.created_at.desc(),
                CrawlRun.id.desc(),
            )
        )
    )

    items = [
        CrawlRunSummaryResponse(
            id=crawl_run.id,
            collection_id=crawl_run.collection_id,
            status=crawl_run.status,
            name=crawl_run.name,
            seed_urls=crawl_run.seed_urls,
            research_intent=crawl_run.research_intent,
            created_at=crawl_run.created_at,
            started_at=crawl_run.started_at,
            completed_at=crawl_run.completed_at,
            job_counts=await get_job_counts(
                session,
                crawl_run.id,
            ),
        )
        for crawl_run in crawl_runs
    ]

    return CrawlRunListResponse(
        total=len(items),
        items=items,
    )


@router.get(
    "/{crawl_run_id}",
    response_model=CrawlRunDetailResponse,
)
async def get_crawl_run(
    collection_id: UUID,
    crawl_run_id: UUID,
    session: AsyncSession = Depends(get_db_session),
) -> CrawlRunDetailResponse:
    crawl_run = await get_crawl_run_for_collection(
        session,
        collection_id=collection_id,
        crawl_run_id=crawl_run_id,
    )

    return CrawlRunDetailResponse(
        id=crawl_run.id,
        collection_id=crawl_run.collection_id,
        status=crawl_run.status,
        name=crawl_run.name,
        research_intent=crawl_run.research_intent,
        job_counts=await get_job_counts(session, crawl_run.id),
        created_at=crawl_run.created_at,
    )


@router.get(
    "/{crawl_run_id}/frontier",
    response_model=CrawlFrontierListResponse,
)
async def list_crawl_frontier(
    collection_id: UUID,
    crawl_run_id: UUID,
    session: AsyncSession = Depends(get_db_session),
) -> CrawlFrontierListResponse:
    await get_crawl_run_for_collection(
        session,
        collection_id=collection_id,
        crawl_run_id=crawl_run_id,
    )

    parent_job = aliased(CrawlJob)

    rows = (
        await session.execute(
            select(
                CrawlJob,
                parent_job.normalized_url.label("parent_url"),
            )
            .outerjoin(
                parent_job,
                CrawlJob.parent_job_id == parent_job.id,
            )
            .where(CrawlJob.crawl_run_id == crawl_run_id)
            .order_by(
                CrawlJob.depth.asc(),
                CrawlJob.priority_score.desc(),
                CrawlJob.created_at.asc(),
                CrawlJob.id.asc(),
            )
        )
    ).all()

    items = [
        CrawlFrontierJobResponse(
            id=job.id,
            parent_job_id=job.parent_job_id,
            parent_url=parent_url,
            original_url=job.original_url,
            normalized_url=job.normalized_url,
            domain=job.domain,
            depth=job.depth,
            anchor_text=job.anchor_text,
            priority_score=job.priority_score,
            priority_band=job.priority_band,
            discovery_reason=job.discovery_reason,
            status=job.status,
            attempt_count=job.attempt_count,
            max_attempts=job.max_attempts,
            last_claimed_by_worker_id=(
                job.last_claimed_by_worker_id
            ),
            last_error_code=job.last_error_code,
            last_error_message=job.last_error_message,
            http_status_code=job.http_status_code,
            fetched_bytes=job.fetched_bytes,
            fetch_duration_ms=job.fetch_duration_ms,
            started_at=job.started_at,
            finished_at=job.finished_at,
            created_at=job.created_at,
        )
        for job, parent_url in rows
    ]

    return CrawlFrontierListResponse(
        total=len(items),
        items=items,
    )


@router.get(
    "/{crawl_run_id}/documents/{document_id}",
    response_model=CrawledDocumentDetailResponse,
)
async def get_crawled_document(
    collection_id: UUID,
    crawl_run_id: UUID,
    document_id: UUID,
    session: AsyncSession = Depends(get_db_session),
) -> CrawledDocumentDetailResponse:
    crawl_run = await get_crawl_run_for_collection(
        session,
        collection_id=collection_id,
        crawl_run_id=crawl_run_id,
    )

    row = (
        await session.execute(
            select(
                CrawledDocument,
                CrawlJob.normalized_url,
                CrawlJob.original_url,
            )
            .join(
                CrawlJob,
                CrawlJob.id == CrawledDocument.crawl_job_id,
            )
            .where(
                CrawlJob.crawl_run_id == crawl_run.id,
                CrawledDocument.id == document_id,
            )
        )
    ).one_or_none()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Crawled document not found for this campaign.",
        )

    document, source_url, original_url = row

    return CrawledDocumentDetailResponse(
        id=document.id,
        crawl_job_id=document.crawl_job_id,
        source_url=source_url,
        original_url=original_url,
        title=document.title,
        extracted_text=document.extracted_text,
        raw_object_key=document.raw_object_key,
        content_type=document.content_type,
        created_at=document.created_at,
    )


@router.get(
    "/{crawl_run_id}/documents",
    response_model=CrawledDocumentListResponse,
)
async def list_crawled_documents(
    collection_id: UUID,
    crawl_run_id: UUID,
    session: AsyncSession = Depends(get_db_session),
) -> CrawledDocumentListResponse:
    crawl_run = await get_crawl_run_for_collection(
        session,
        collection_id=collection_id,
        crawl_run_id=crawl_run_id,
    )

    rows = (
        await session.execute(
            select(
                CrawledDocument,
                CrawlJob.normalized_url,
            )
            .join(
                CrawlJob,
                CrawlJob.id == CrawledDocument.crawl_job_id,
            )
            .where(CrawlJob.crawl_run_id == crawl_run.id)
            .order_by(CrawledDocument.created_at.desc())
        )
    ).all()

    items = [
        CrawledDocumentResponse(
            id=document.id,
            crawl_job_id=document.crawl_job_id,
            source_url=source_url,
            title=document.title,
            extracted_text_preview=(
                document.extracted_text[:500]
                if document.extracted_text is not None
                else None
            ),
            raw_object_key=document.raw_object_key,
            content_type=document.content_type,
            created_at=document.created_at,
        )
        for document, source_url in rows
    ]

    return CrawledDocumentListResponse(
        total=len(items),
        items=items,
    )


@router.post(
    "",
    response_model=CrawlRunResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_crawl_run(
    collection_id: UUID,
    payload: CrawlRunCreateRequest,
    response: Response,
    idempotency_key: IdempotencyKey,
    session: AsyncSession = Depends(get_db_session),
) -> CrawlRunResponse:
    collection = await session.get(Collection, collection_id)

    if collection is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="collection not found",
        )

    normalized_idempotency_key = idempotency_key.strip()

    if not normalized_idempotency_key:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Idempotency-Key must not be blank",
        )

    allowed_domains = list(
        dict.fromkeys(
            normalize_allowed_domain(domain)
            for domain in payload.allowed_domains
        )
    )

    seed_jobs: list[tuple[str, str, str]] = []
    seen_urls: set[str] = set()

    for seed_url in payload.seed_urls:
        original_url = seed_url.strip()
        normalized_url, domain = normalize_seed_url(original_url)

        if not domain_is_allowed(domain, allowed_domains):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    f"seed domain {domain!r} is not in "
                    "allowed_domains"
                ),
            )

        if normalized_url in seen_urls:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"duplicate normalized seed URL: {normalized_url}",
            )

        seen_urls.add(normalized_url)
        seed_jobs.append((original_url, normalized_url, domain))

    existing_run = await session.scalar(
        select(CrawlRun).where(
            CrawlRun.collection_id == collection_id,
            CrawlRun.idempotency_key == normalized_idempotency_key,
        )
    )

    if existing_run is not None:
        if not same_request(
            existing_run,
            seed_urls=[job[1] for job in seed_jobs],
            allowed_domains=allowed_domains,
            research_intent=payload.research_intent,
            payload=payload,
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Idempotency-Key has already been used with a "
                    "different crawl request"
                ),
            )

        response.status_code = status.HTTP_200_OK
        return await to_response(session, existing_run)

    crawl_run = CrawlRun(
        collection_id=collection_id,
        name=payload.name,
        seed_urls=[job[1] for job in seed_jobs],
        allowed_domains=allowed_domains,
        research_intent=payload.research_intent,
        max_pages=payload.max_pages,
        max_depth=payload.max_depth,
        request_timeout_seconds=payload.request_timeout_seconds,
        max_attempts=payload.max_attempts,
        idempotency_key=normalized_idempotency_key,
    )
    session.add(crawl_run)

    try:
        await session.flush()

        session.add_all(
            [
                CrawlJob(
                    crawl_run_id=crawl_run.id,
                    original_url=original_url,
                    normalized_url=normalized_url,
                    domain=domain,
                    depth=0,
                    max_attempts=payload.max_attempts,
                    priority_score=1_000_001,
                    priority_band="HIGH",
                    discovery_reason="campaign seed",
                )
                for original_url, normalized_url, domain in seed_jobs
            ]
        )

        await session.commit()
    except IntegrityError as exc:
        await session.rollback()

        existing_run = await session.scalar(
            select(CrawlRun).where(
                CrawlRun.collection_id == collection_id,
                CrawlRun.idempotency_key == normalized_idempotency_key,
            )
        )

        if existing_run is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="unable to create crawl run",
            ) from exc

        if not same_request(
            existing_run,
            seed_urls=[job[1] for job in seed_jobs],
            allowed_domains=allowed_domains,
            research_intent=payload.research_intent,
            payload=payload,
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Idempotency-Key has already been used with a "
                    "different crawl request"
                ),
            ) from exc

        response.status_code = status.HTTP_200_OK
        return await to_response(session, existing_run)

    await session.refresh(crawl_run)

    return await to_response(session, crawl_run)


@router.post(
    "/{crawl_run_id}/brief",
    response_model=CampaignBriefResponse,
)
async def generate_campaign_brief_for_campaign(
    collection_id: UUID,
    crawl_run_id: UUID,
    session: AsyncSession = Depends(get_db_session),
) -> CampaignBriefResponse:
    """
    Generate or retrieve one bounded, source-linked campaign brief.

    The route invokes Bedrock only after PostgreSQL atomically reserves a
    GENERATING row. READY rows are returned from cache. FAILED rows are
    retried only when the caller explicitly submits this POST again.
    """
    crawl_run = await get_crawl_run_for_collection(
        session,
        collection_id=collection_id,
        crawl_run_id=crawl_run_id,
    )

    if crawl_run.status not in {"SUCCEEDED", "PARTIALLY_SUCCEEDED"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "campaign briefs require a completed campaign; "
                f"current status is {crawl_run.status}"
            ),
        )

    try:
        reservation = await reserve_campaign_brief(
            session,
            crawl_run=crawl_run,
        )
        await session.commit()
    except NoUsableCampaignEvidenceError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc

    brief = reservation.brief
    should_generate = reservation.created

    if brief.status == "FAILED":
        should_generate = await claim_failed_campaign_brief_for_retry(
            session,
            brief_id=brief.id,
        )
        await session.commit()
        await session.refresh(brief)

    if not should_generate:
        return to_campaign_brief_response(brief)

    settings = get_settings()

    region_name = (
        getattr(settings, "aws_region", None)
        or settings.s3_region_name
    ).strip()

    try:
        generated = await generate_campaign_brief_from_plan(
            evidence_plan=reservation.evidence_plan,
            model_id=brief.model_id,
            region_name=region_name,
        )
    except Exception:
        brief = await mark_campaign_brief_failed(
            session,
            brief_id=brief.id,
            error_code="BEDROCK_GENERATION_FAILED",
            error_message=(
                "The campaign brief request did not produce "
                "a valid result."
            ),
        )
        return to_campaign_brief_response(brief)

    brief = await mark_campaign_brief_ready(
        session,
        brief_id=brief.id,
        brief_json=generated.brief_json,
        output_token_count=generated.output_token_count,
    )

    return to_campaign_brief_response(brief)
