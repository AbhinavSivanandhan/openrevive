from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.briefing.evidence_packing import (
    PROMPT_VERSION,
    EvidenceBundle,
    EvidenceDocument,
    build_evidence_bundle,
)
from app.models.campaign_brief import CampaignBrief
from app.models.crawled_document import CrawledDocument
from app.models.crawl_job import CrawlJob
from app.models.crawl_run import CrawlRun


DEFAULT_BRIEF_MODEL_ID = "apac.amazon.nova-micro-v1:0"


class NoUsableCampaignEvidenceError(ValueError):
    """The campaign has no persisted extracted text to brief."""


@dataclass(frozen=True, slots=True)
class CampaignBriefReservation:
    brief: CampaignBrief
    evidence_bundle: EvidenceBundle
    created: bool


async def load_campaign_evidence_documents(
    session: AsyncSession,
    *,
    crawl_run_id: UUID,
) -> list[EvidenceDocument]:
    rows = (
        await session.execute(
            select(
                CrawledDocument.id,
                CrawlJob.normalized_url,
                CrawledDocument.content_sha256,
                CrawledDocument.title,
                CrawledDocument.extracted_text,
            )
            .join(
                CrawlJob,
                CrawlJob.id == CrawledDocument.crawl_job_id,
            )
            .where(CrawlJob.crawl_run_id == crawl_run_id)
            .order_by(
                CrawlJob.normalized_url.asc(),
                CrawledDocument.id.asc(),
            )
        )
    ).all()

    return [
        EvidenceDocument(
            id=document_id,
            source_url=source_url,
            content_sha256=content_sha256,
            title=title,
            extracted_text=extracted_text,
        )
        for (
            document_id,
            source_url,
            content_sha256,
            title,
            extracted_text,
        ) in rows
    ]


async def reserve_campaign_brief(
    session: AsyncSession,
    *,
    crawl_run: CrawlRun,
    model_id: str = DEFAULT_BRIEF_MODEL_ID,
) -> CampaignBriefReservation:
    """
    Atomically reserve exactly one AI brief per immutable evidence fingerprint.

    The caller that creates the row will later invoke Bedrock. Every later
    request for the same corpus receives the existing GENERATING, READY, or
    FAILED row and must not make another model call.
    """
    documents = await load_campaign_evidence_documents(
        session,
        crawl_run_id=crawl_run.id,
    )

    evidence_bundle = build_evidence_bundle(
        documents=documents,
        research_intent=crawl_run.research_intent,
        model_id=model_id,
        prompt_version=PROMPT_VERSION,
    )

    if evidence_bundle.input_document_count == 0:
        raise NoUsableCampaignEvidenceError(
            "campaign has no persisted extracted text to brief"
        )

    inserted_brief_id = await session.scalar(
        insert(CampaignBrief)
        .values(
            crawl_run_id=crawl_run.id,
            corpus_fingerprint=evidence_bundle.corpus_fingerprint,
            model_id=model_id,
            prompt_version=PROMPT_VERSION,
            status="GENERATING",
            input_document_count=(
                evidence_bundle.input_document_count
            ),
            input_character_count=(
                evidence_bundle.input_character_count
            ),
        )
        .on_conflict_do_nothing(
            constraint="crawl_run_corpus_fingerprint_unique"
        )
        .returning(CampaignBrief.id)
    )

    if inserted_brief_id is not None:
        brief = await session.get(
            CampaignBrief,
            inserted_brief_id,
        )

        if brief is None:
            raise RuntimeError(
                "campaign brief reservation disappeared"
            )

        return CampaignBriefReservation(
            brief=brief,
            evidence_bundle=evidence_bundle,
            created=True,
        )

    brief = await session.scalar(
        select(CampaignBrief).where(
            CampaignBrief.crawl_run_id == crawl_run.id,
            CampaignBrief.corpus_fingerprint
            == evidence_bundle.corpus_fingerprint,
        )
    )

    if brief is None:
        raise RuntimeError(
            "campaign brief reservation could not be read"
        )

    return CampaignBriefReservation(
        brief=brief,
        evidence_bundle=evidence_bundle,
        created=False,
    )


async def claim_failed_campaign_brief_for_retry(
    session: AsyncSession,
    *,
    brief_id: UUID,
) -> bool:
    """
    Atomically convert one FAILED brief back to GENERATING.

    A retry is only caused by an explicit API request. Concurrent retry
    requests are safe: exactly one caller wins this conditional update.
    """
    retried_brief_id = await session.scalar(
        update(CampaignBrief)
        .where(
            CampaignBrief.id == brief_id,
            CampaignBrief.status == "FAILED",
        )
        .values(
            status="GENERATING",
            error_code=None,
            error_message=None,
            completed_at=None,
            output_token_count=None,
            brief_json=None,
        )
        .returning(CampaignBrief.id)
    )

    return retried_brief_id is not None


async def mark_campaign_brief_ready(
    session: AsyncSession,
    *,
    brief_id: UUID,
    brief_json: dict[str, object],
    output_token_count: int | None,
) -> CampaignBrief:
    """
    Persist a validated one-call Bedrock result.

    This helper commits its own state transition. The route may already have
    an implicit SQLAlchemy transaction from reading or refreshing the brief.
    """

    brief = await session.scalar(
        select(CampaignBrief)
        .where(CampaignBrief.id == brief_id)
        .with_for_update()
    )

    if brief is None:
        raise RuntimeError("campaign brief disappeared")

    if brief.status != "GENERATING":
        await session.commit()
        await session.refresh(brief)
        return brief

    database_now = await session.scalar(select(func.now()))

    if database_now is None:
        raise RuntimeError("database did not return a timestamp")

    brief.status = "READY"
    brief.brief_json = brief_json
    brief.output_token_count = output_token_count
    brief.error_code = None
    brief.error_message = None
    brief.completed_at = database_now

    await session.commit()
    await session.refresh(brief)

    return brief


async def mark_campaign_brief_failed(
    session: AsyncSession,
    *,
    brief_id: UUID,
    error_code: str,
    error_message: str,
) -> CampaignBrief:
    """
    Persist a terminal generation failure.

    Failed briefs are never retried automatically. A later explicit POST
    request may atomically claim the row for one manual retry.
    """

    brief = await session.scalar(
        select(CampaignBrief)
        .where(CampaignBrief.id == brief_id)
        .with_for_update()
    )

    if brief is None:
        raise RuntimeError("campaign brief disappeared")

    if brief.status != "GENERATING":
        await session.commit()
        await session.refresh(brief)
        return brief

    database_now = await session.scalar(select(func.now()))

    if database_now is None:
        raise RuntimeError("database did not return a timestamp")

    brief.status = "FAILED"
    brief.brief_json = None
    brief.output_token_count = None
    brief.error_code = error_code
    brief.error_message = error_message
    brief.completed_at = database_now

    await session.commit()
    await session.refresh(brief)

    return brief
