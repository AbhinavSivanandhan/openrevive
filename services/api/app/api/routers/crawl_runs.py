from __future__ import annotations

from datetime import datetime
from typing import Annotated
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

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
    max_pages: int = Field(gt=0, le=10_000)
    max_depth: int = Field(ge=0, le=20)
    request_timeout_seconds: int = Field(gt=0, le=120)
    max_attempts: int = Field(gt=0, le=10)


class CrawlRunResponse(BaseModel):
    id: UUID
    collection_id: UUID
    status: str
    seed_urls: list[str]
    allowed_domains: list[str]
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
    job_counts: dict[str, int]
    created_at: datetime


class CrawledDocumentResponse(BaseModel):
    id: UUID
    crawl_job_id: UUID
    source_url: str
    title: str | None
    extracted_text_preview: str | None
    raw_object_key: str
    content_type: str
    created_at: datetime


class CrawledDocumentListResponse(BaseModel):
    total: int
    items: list[CrawledDocumentResponse]


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
        port = parsed.port
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

    default_port = 80 if scheme == "http" else 443
    host_for_netloc = host if ":" not in host else f"[{host}]"

    if port is not None and port != default_port:
        netloc = f"{host_for_netloc}:{port}"
    else:
        netloc = host_for_netloc

    normalized_url = urlunsplit(
        (
            scheme,
            netloc,
            parsed.path or "/",
            parsed.query,
            "",
        )
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
    payload: CrawlRunCreateRequest,
) -> bool:
    return (
        crawl_run.seed_urls == seed_urls
        and crawl_run.allowed_domains == allowed_domains
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
        seed_urls=crawl_run.seed_urls,
        allowed_domains=crawl_run.allowed_domains,
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
        status_value: int(count)
        for status_value, count in rows
    }
    job_counts["TOTAL"] = sum(job_counts.values())

    return job_counts


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
        job_counts=await get_job_counts(session, crawl_run.id),
        created_at=crawl_run.created_at,
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
        seed_urls=[job[1] for job in seed_jobs],
        allowed_domains=allowed_domains,
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

        response.status_code = status.HTTP_200_OK
        return await to_response(session, existing_run)

    await session.refresh(crawl_run)

    return await to_response(session, crawl_run)
